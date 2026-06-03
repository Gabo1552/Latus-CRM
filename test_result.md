#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Real business-hours logic + scheduled scanning for "Lead sin respuesta" in Latus CRM:
  - Extend settings (business_hours_start/end, business_days, business_timezone) + use existing
    lead_no_response_business_hours_only flag.
  - Pure utility module backend/utils/business_hours.py (is_within_business_hours,
    business_seconds_between) using zoneinfo; UTC storage, TZ math at the boundary.
  - When business-only is true, elapsed time = business seconds only; defer creation until
    inside business hours; preserve idempotency.
  - APScheduler job every 5 min (started in FastAPI lifespan, safe-start).
  - Frontend Admin (Spanish): toggle, start/end time, weekday checkboxes, tz select.
  - Pytest tests covering 6 acceptance scenarios.

backend:
  - task: "business_hours utility module + tests"
    implemented: true
    working: true
    file: "backend/utils/business_hours.py, backend/tests/test_business_hours.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "10/10 unit tests pass: business_seconds_between (intra-window, clipping, zero), weekend handling (Fri 17:00 -> Mon 10:00 = 2h, Fri 18:00 -> Mon 10:00 = 1h), timezone shift (Cordoba vs UTC), no-alert-outside-hours (Saturday), alert-when-threshold-crossed, idempotency (scan x2 = 1 notif)."
  - task: "scan_lead_no_response: real business-hours logic"
    implemented: true
    working: true
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "When lead_no_response_business_hours_only is true, elapsed time is business_seconds_between(last_contact, now). Notification fires only when threshold crossed AND now is inside business hours; otherwise deferred. When flag is false, behavior unchanged. Settings extended with business_hours_start/end (HH:MM), business_days [int], business_timezone (IANA), validated in PATCH /api/settings."
  - task: "APScheduler: scheduled 5-min lead_no_response scan"
    implemented: true
    working: true
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "AsyncIOScheduler started in FastAPI startup; singleton guard so reloads don't double-run; logs confirm scheduled job 'lead_no_response_scan' executes every 5m. Dashboard-triggered scan kept as fallback (same code path)."

frontend:
  - task: "Admin business-hours settings UI (Spanish)"
    implemented: true
    working: "NA"
    file: "frontend/src/pages/Admin.jsx"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Added Activar horario laboral toggle, Hora de inicio / Hora de fin time inputs, Días laborales chip buttons (Lun-Dom), Zona horaria select with 6 IANA options (default America/Argentina/Cordoba). Block visually de-emphasized when toggle is OFF. Persists via existing PATCH /api/settings."

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 1
  run_ui: false

test_plan:
  current_focus:
    - "scan_lead_no_response: real business-hours logic"
    - "APScheduler: scheduled 5-min lead_no_response scan"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "Implemented business-hours-aware lead_no_response automation + APScheduler. Unit-tested via backend/tests/test_business_hours.py (10/10 pass). Frontend Admin settings extended. /api/openapi.json exposed at /api/openapi.json. test_credentials.md updated."