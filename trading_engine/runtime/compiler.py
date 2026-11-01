"""
Dynamic Indicator Compiler and AST Security Validator.
Validates code safety, parses syntax errors with line/column coordinates,
and loads user-authored Indicator classes into sandboxed module namespaces.
"""
import ast
import types
from typing import Dict, Any, Tuple, Optional, Type
from ..quant_lib.base import Indicator, InputDescriptor, IndicatorSignal


class SecurityValidationError(Exception):
    """Raised when user code contains prohibited modules or dangerous operations."""
    def __init__(self, message: str, line: int = 0, column: int = 0):
        super().__init__(f"Line {line}, Col {column}: {message}" if line > 0 else message)
        self.message = message
        self.line = line
        self.column = column


class IndicatorCompiler:
    """
    Sandboxed compiler that inspects AST, compiles valid Python strings,
    and extracts runnable Indicator classes with dynamic parameter schemas.
    """
    DISALLOWED_MODULES = {
        "os", "subprocess", "sys", "shutil", "socket", "http",
        "urllib", "requests", "ctypes", "multiprocessing", "pickle"
    }
    DISALLOWED_BUILTINS = {
        "exec", "eval", "compile", "__import__", "open", "input",
        "globals", "locals", "breakpoint"
    }

    @classmethod
    def validate_ast(cls, code_str: str) -> None:
        """
        Parses AST and rejects prohibited imports or function calls.
        Raises SecurityValidationError on violation.
        """
        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            raise SecurityValidationError(f"SyntaxError: {e.msg}", line=e.lineno or 0, column=e.offset or 0)

        for node in ast.walk(tree):
            # 1. Check direct imports: import os
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in cls.DISALLOWED_MODULES:
                        raise SecurityValidationError(
                            f"Prohibited import: '{root_mod}'",
                            line=getattr(node, "lineno", 0),
                            column=getattr(node, "col_offset", 0)
                        )

            # 2. Check from imports: from os import system
            elif isinstance(node, ast.ImportFrom):
                root_mod = (node.module or "").split(".")[0]
                if root_mod in cls.DISALLOWED_MODULES:
                    raise SecurityValidationError(
                        f"Prohibited import: '{root_mod}'",
                        line=getattr(node, "lineno", 0),
                        column=getattr(node, "col_offset", 0)
                    )

            # 3. Check prohibited calls: eval(), open()
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in cls.DISALLOWED_BUILTINS:
                    raise SecurityValidationError(
                        f"Prohibited built-in call: '{node.func.id}()'",
                        line=getattr(node, "lineno", 0),
                        column=getattr(node, "col_offset", 0)
                    )

    @classmethod
    def compile_and_load(
        cls, code_str: str, module_name: str = "custom_indicator"
    ) -> Tuple[Type[Indicator], Dict[str, Any]]:
        """
        Validates, compiles, and extracts the Indicator class and its parameter schema.
        Returns (indicator_class, param_schema_dict).
        """
        cls.validate_ast(code_str)

        # Prepare isolated sandbox namespace
        import sys
        module = types.ModuleType(module_name)
        from .. import quant_lib
        sys.modules["quant_lib"] = quant_lib
        module.__dict__["quant_lib"] = quant_lib
        module.__dict__["ta"] = quant_lib.ta
        module.__dict__["structure"] = quant_lib.structure
        module.__dict__["order_flow"] = quant_lib.order_flow
        module.__dict__["options"] = quant_lib.options
        module.__dict__["input"] = quant_lib.input
        module.__dict__["Indicator"] = quant_lib.Indicator
        module.__dict__["BaseIndicator"] = quant_lib.BaseIndicator
        module.__dict__["IndicatorSignal"] = quant_lib.IndicatorSignal

        try:
            compiled_bytecode = compile(code_str, filename=f"<{module_name}>", mode="exec")
            exec(compiled_bytecode, module.__dict__)
        except Exception as e:
            raise RuntimeError(f"Runtime execution error: {str(e)}")

        # Discover class subclassing Indicator
        indicator_cls = None
        for item_name, item_val in module.__dict__.items():
            if (
                isinstance(item_val, type)
                and item_val.__name__ not in ("Indicator", "BaseIndicator")
                and any(base.__name__ in ("Indicator", "BaseIndicator") for base in item_val.__mro__)
            ):
                indicator_cls = item_val
                break

        if not indicator_cls:
            raise ValueError("No class inheriting from 'quant_lib.Indicator' was found in the script.")

        # Instantiate temporary instance to extract input parameter schema
        temp_instance = indicator_cls()
        param_schema = cls.extract_param_schema(temp_instance)
        return indicator_cls, param_schema

    @classmethod
    def extract_param_schema(cls, instance: Indicator) -> Dict[str, Any]:
        """Extracts all InputDescriptor instances from the indicator into a UI schema."""
        schema = {}
        for attr_name, attr_val in instance.__dict__.items():
            if isinstance(attr_val, InputDescriptor):
                schema[attr_name] = {
                    "name": attr_name,
                    "title": attr_val.title,
                    "type": attr_val.input_type,
                    "default": attr_val.default,
                    "current": attr_val.value,
                    "minval": attr_val.minval,
                    "maxval": attr_val.maxval,
                    "step": attr_val.step,
                    "options": attr_val.options,
                    "group": attr_val.group,
                    "tooltip": attr_val.tooltip,
                }
        return schema
