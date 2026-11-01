# BUG 13: Dedicated Watch Window Tabs & Accessible Sidebar Layout

> **Status:** Ready for Implementation  
> **Severity:** High (Workflow & Usability)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description
The Variable Watch window was placed at the very bottom of the left sidebar underneath the "SCRIPT INPUTS" section. Because complex strategies like BSS Pro v5 declare 15+ input sliders, the entire sidebar was consumed by sliders, pushing the Variable Watch table completely off-screen below the scroll fold where users could not see it.

## 2. Root Cause Analysis
In `studio.html`, the sidebar was a single continuous vertical stack with no tabs or accordion controls:
`Header -> SCRIPT INPUTS (15+ sliders) -> VARIABLE WATCH -> BREAKPOINTS`.
When inputs were generated dynamically, their height exceeded the sidebar container height, hiding the Watch table.

## 3. Solution Architecture & Technical Changes
1. **Sidebar Navigation Tabs (`studio.html`):**
   - Add top tabs to the left sidebar:
     - `[ 🔍 Watch ]` (Active by default for inspection)
     - `[ ⚙️ Inputs ]` (Dynamic input sliders)
     - `[ 🔴 Breakpoints ]` (Active breakpoint list)
2. **Dedicated Full-Height Panel for Each View (`studio.css`):**
   - When the user selects `[ 🔍 Watch ]`, the Watch table occupies 100% of the sidebar height with a prominent "Add Expression" input bar at the top and real-time variable evaluations.
   - When the user selects `[ ⚙️ Inputs ]`, the sliders are displayed with clean scroll.
   - Users can toggle between views instantly with a single click.

## 4. Acceptance Criteria
- [ ] Variable Watch table is immediately visible on screen upon loading and never buried under sliders.
- [ ] Users can toggle between Watch, Inputs, and Breakpoints via dedicated sidebar tabs.
- [ ] Variables update dynamically on every tick and replay step.
