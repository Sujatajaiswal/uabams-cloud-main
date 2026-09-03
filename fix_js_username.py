import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Make it editable when opening edit modal
js = js.replace("document.getElementById('userUsername').readOnly = true;", "document.getElementById('userUsername').readOnly = false;")

# Include username in PUT payload
put_target = """        if (id) {
          // Update existing user
          const payload = {
            role,
            can_view_alerts: canViewAlerts,
            can_configure_thresholds: canConfigureThresholds,
            can_manage_users: canManageUsers,
            is_active: isActive
          };"""

put_insertion = """        if (id) {
          // Update existing user
          const payload = {
            username,
            role,
            can_view_alerts: canViewAlerts,
            can_configure_thresholds: canConfigureThresholds,
            can_manage_users: canManageUsers,
            is_active: isActive
          };"""

if "username,\n            role," not in js:
    js = js.replace(put_target, put_insertion)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
