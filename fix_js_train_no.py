import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Helper function
helper_code = """function extractTrainNo(val) {
  if (!val) return '';
  return val.split(' - ')[0].trim();
}

function trainNoValue() {
  return extractTrainNo($('trainNo')?.value);
}"""

js = re.sub(r'function trainNoValue\(\) \{.*?(?=\n\})\}', helper_code, js, flags=re.DOTALL)

# Now update the reports
js = js.replace("const rid = $('ridInput').value.trim();", "const rid = extractTrainNo($('ridInput').value);")
js = js.replace("const rid = $('repRidInput').value.trim();", "const rid = extractTrainNo($('repRidInput').value);")
js = js.replace("let rid = $('graphRid').value.trim();", "let rid = extractTrainNo($('graphRid').value);")

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
