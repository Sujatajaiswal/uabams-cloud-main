import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Replace the end of the function manually
# I will use a regex that matches from `const errHtml = ` to the end of the loadLogs function

pattern = re.compile(r'const errHtml = log\.errorMessage && log\.errorMessage !== \'-\' \? `.*?\}\s*\}', re.DOTALL)

replacement = """let errHtml = '-';
        if (log.errorMessage && log.errorMessage !== '-') {
          if (severity === 'CRITICAL' && (log.action.includes('Reset') || log.action.includes('Cleanup'))) {
            errHtml = `<span style="color: #059669; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          } else {
            errHtml = `<span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          }
        }
  
        return `
          <tr>
            <td>${formatDate(log.createdAt)}</td>
            <td>${escapeHtml(log.username || '-')}</td>
            <td>${escapeHtml(log.page || '-')}</td>
            <td>${escapeHtml(log.action || '-')}</td>
            <td>${badgeHtml}</td>
            <td>${errHtml}</td>
            <td>${escapeHtml(log.ipAddress || '-')}</td>
            <td>${log.latitude && log.longitude ? `${log.latitude}, ${log.longitude}` : '-'}</td>
          </tr>
        `;
      }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    } finally {
      if (btn) {
        btn.textContent = 'Refresh';
        btn.disabled = false;
      }
    }
  }"""

js_new = pattern.sub(replacement, js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js_new)
