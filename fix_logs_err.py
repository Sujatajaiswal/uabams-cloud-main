import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = """        const errHtml = log.errorMessage && log.errorMessage !== '-' ? `
          <span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>
        ` : '-';"""

replacement = """        let errHtml = '-';
        if (log.errorMessage && log.errorMessage !== '-') {
          if (severity === 'CRITICAL' && (log.action.includes('Reset') || log.action.includes('Cleanup'))) {
            errHtml = `<span style="color: #059669; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          } else {
            errHtml = `<span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          }
        }"""

js = js.replace(target, replacement)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
