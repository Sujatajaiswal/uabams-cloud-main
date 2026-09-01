import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Add initialization and constraints
target = """  $('toDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("fromDate", "toDate"));"""

insertion = """
  DateUtils.initializeDefaultDates("cleanupStart", "cleanupEnd");
  DateUtils.applyDateRangeConstraints("cleanupStart", "cleanupEnd");
  $('cleanupStart')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("cleanupStart", "cleanupEnd"));
  $('cleanupEnd')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("cleanupStart", "cleanupEnd"));
"""

js = js.replace(target, target + insertion)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
