import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

func_pattern = re.compile(r'async function loadLogs\(\)\s*\{.*?\n  \}', re.DOTALL)

new_func = """async function loadLogs() {
    const btn = $('loadLogsBtn');
    if (btn) {
      btn.textContent = 'Refreshing...';
      btn.disabled = true;
    }
    try {
      const data = await requestJson('/api/v1/logs?limit=100&_t=' + Date.now());
      const rows = data.logs || [];
      
      const getLogSeverity = (log) => {
        const act = String(log.action || '').toLowerCase();
        const err = String(log.errorMessage || '').toLowerCase();
        const hasError = (err && err !== '-' && err !== 'none' && err !== 'null');
        if (act.includes('delete') || act.includes('cleanup') || act.includes('remove') || act.includes('reset') || act.includes('failed') || act.includes('unauthorized')) return 'CRITICAL';
        if (act.includes('login') || act.includes('logout') || act.includes('calibrate') || act.includes('export')) return hasError ? 'WARNING' : 'NORMAL';
        if (hasError) return 'CRITICAL';
        return 'NORMAL';
      };
  
      setHtml('logsTable', rows.length ? rows.map((log) => {
        const severity = getLogSeverity(log);
        let badgeStyle = '';
        if (severity === 'CRITICAL') {
          badgeStyle = 'background: rgba(239, 68, 68, 0.12); color: #dc2626; border: 1px solid rgba(239, 68, 68, 0.35);';
        } else if (severity === 'WARNING') {
          badgeStyle = 'background: rgba(245, 158, 11, 0.12); color: #d97706; border: 1px solid rgba(245, 158, 11, 0.35);';
        } else {
          badgeStyle = 'background: rgba(16, 185, 129, 0.12); color: #059669; border: 1px solid rgba(16, 185, 129, 0.35);';
        }
        
        const badgeHtml = `<span style="padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block; text-transform: uppercase; ${badgeStyle}">${severity}</span>`;
        
        let errHtml = '-';
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
        btn.textContent = 'Refresh Logs';
        btn.disabled = false;
      }
    }
  }"""

js = func_pattern.sub(new_func, js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
