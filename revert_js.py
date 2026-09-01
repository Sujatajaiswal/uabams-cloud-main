with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

flatpickr_code = """  if (window.flatpickr) {
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
  }\n"""

js = js.replace(flatpickr_code, '')

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
