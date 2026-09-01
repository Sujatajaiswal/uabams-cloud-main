import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

loadlogs_find = """          </tr>
        `;
      }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
    }"""
loadlogs_catch = """          </tr>
        `;
      }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
    } catch (error) {
      console.error('Logs Error:', error);
      setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
    }"""

js = js.replace(loadlogs_find, loadlogs_catch)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
