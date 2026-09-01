import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

helper = """function extractTrainNo(val) {
  if (!val) return '';
  return val.split(' - ')[0].trim();
}
"""

js = helper + js

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
