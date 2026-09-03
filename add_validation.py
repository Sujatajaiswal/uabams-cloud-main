import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = """async function saveCalibration(gatewayId) {
  const card = cardFor(gatewayId);
  const output = card?.querySelector('[data-role="calOutput"]');"""

replacement = """async function saveCalibration(gatewayId) {
  const card = cardFor(gatewayId);
  const output = card?.querySelector('[data-role="calOutput"]');

  // Validate ranges
  if (card) {
    const inputs = card.querySelectorAll('input[type="number"]');
    for (const input of inputs) {
      if (input.value !== '' && !input.checkValidity()) {
        alert(`Invalid value. Please enter a value between ${input.min} and ${input.max}.`);
        input.focus();
        return;
      }
    }
  }"""

js = js.replace(target, replacement)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
