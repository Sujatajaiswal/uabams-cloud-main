import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = "async function loadLogs() {"
replacement = """async function loadLogs() {
  const btn = $('loadLogsBtn');
  if (btn) {
    btn.textContent = 'Refreshing...';
    btn.disabled = true;
  }"""

js = js.replace(target, replacement)

target_catch = """    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    }
  }"""

replacement_catch = """    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    } finally {
      if (btn) {
        btn.textContent = 'Refresh Logs';
        btn.disabled = false;
      }
    }
  }"""

js = js.replace(target_catch, replacement_catch)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
