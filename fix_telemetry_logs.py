import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

target_cleanup = """      await db.pg_pool.execute(
          "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
          username, "/dashboard", f"Data Cleanup - {data.trainNo}", "", ip
      )"""
      
repl_cleanup = """      await db.pg_pool.execute(
          "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
          username, "/dashboard", f"Data Cleanup - {data.trainNo}", f"Removed: Peak({res_peak}), RMS({res_rms}), Alarms({res_alert}), Faults({res_fault})", ip
      )"""

target_reset = """      await db.pg_pool.execute(
          "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
          username, "/dashboard", f"Reset Session - {data.trainNo}", "", ip
      )"""
      
repl_reset = """      await db.pg_pool.execute(
          "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
          username, "/dashboard", f"Reset Session - {data.trainNo}", "Commands queued for gateways. Database reset.", ip
      )"""

py = py.replace(target_cleanup, repl_cleanup)
py = py.replace(target_reset, repl_reset)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
