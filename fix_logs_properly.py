import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = """    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    }
  }"""

replacement = """    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    } finally {
      if (btn) {
        btn.textContent = 'Refresh';
        btn.disabled = false;
      }
    }
  }"""

js = js.replace(target, replacement)

# ALSO fix the errHtml
target_err = """        const errHtml = log.errorMessage && log.errorMessage !== '-' ? `
          <span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>
        ` : '-';"""

replacement_err = """        let errHtml = '-';
        if (log.errorMessage && log.errorMessage !== '-') {
          if (severity === 'CRITICAL' && (log.action.includes('Reset') || log.action.includes('Cleanup'))) {
            errHtml = `<span style="color: #059669; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          } else {
            errHtml = `<span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>`;
          }
        }"""

js = js.replace(target_err, replacement_err)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
