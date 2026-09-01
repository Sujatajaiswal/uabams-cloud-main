import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

def replace_severity(js):
    old_str = "if (act.includes('delete') || act.includes('remove') || act.includes('reset') || act.includes('failed') || act.includes('unauthorized')) return 'CRITICAL';"
    new_str = "if (act.includes('delete') || act.includes('cleanup') || act.includes('remove') || act.includes('reset') || act.includes('failed') || act.includes('unauthorized')) return 'CRITICAL';"
    return js.replace(old_str, new_str)

js = replace_severity(js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
