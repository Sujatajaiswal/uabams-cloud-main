import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    content = f.read()

bad_flatpickr = 'if (window.flatpickr) { flatpickr(\'input[type="datetime-local"]\', { enableTime: true, dateFormat: "Y-m-d\\\\TH:i:S", enableSeconds: true, time_24hr: false }); }'

good_flatpickr = """if (window.flatpickr) {
    document.querySelectorAll('input[type="datetime-local"]').forEach(el => {
      const hasSeconds = el.getAttribute('step') === '1';
      flatpickr(el, {
        enableTime: true,
        enableSeconds: hasSeconds,
        dateFormat: hasSeconds ? "Y-m-d\\\\TH:i:S" : "Y-m-d\\\\TH:i",
        altInput: true,
        altFormat: hasSeconds ? "m/d/Y h:i:S K" : "m/d/Y h:i K"
      });
    });
  }"""

content = content.replace(bad_flatpickr, good_flatpickr)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(content)
