import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

old_html = """          <div class="summary-card">
            <div class="summary-icon Warning-icon">
              <i class="bi bi-tools"></i>
            </div>"""

new_html = """          <div class="summary-card">
            <div class="summary-icon" style="background: #f59e0b;">
              <i class="bi bi-exclamation-circle"></i>
            </div>"""

html = html.replace(old_html, new_html)

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
