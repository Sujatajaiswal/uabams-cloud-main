import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

bad_code = """    }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
  } catch (error) {
    console.error('Logs Error:', error);
    setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
  } catch (error) {
    setHtml('logsTable', `<tr><td colspan="8" class="error-text">${escapeHtml(error.message)}</td></tr>`);
  }"""

good_code = """    }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
  } catch (error) {
    console.error('Logs Error:', error);
    setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
  }"""

js = js.replace(bad_code, good_code)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
