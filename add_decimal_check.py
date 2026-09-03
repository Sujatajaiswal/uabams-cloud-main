import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = """    // Validate ranges
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

replacement = """    // Validate ranges and decimals
  if (card) {
    const inputs = card.querySelectorAll('input[type="number"]');
    for (const input of inputs) {
      if (input.value !== '') {
        if (!input.checkValidity()) {
          alert(`Invalid value. Please enter a value between ${input.min} and ${input.max}.`);
          input.focus();
          return;
        }
        if (input.dataset.field === 'triggerStartSpeedKmph' && !input.value.includes('.')) {
          alert(`Trigger Start Speed must be a decimal value (e.g., 20.0 instead of 20).`);
          input.focus();
          return;
        }
        if (input.dataset.field === 'wheelDiameterM' && !input.value.includes('.')) {
          alert(`Wheel Diameter must be a decimal value (e.g., 0.915 instead of 1).`);
          input.focus();
          return;
        }
      }
    }
  }"""

# Fix indentation in python script
target = target.replace("  if (card)", "  if (card)").replace("    const", "    const")

js = re.sub(r'\s*// Validate ranges\s*if \(card\) \{\s*const inputs = card\.querySelectorAll\(\'input\[type="number"\]\'\);\s*for \(const input of inputs\) \{\s*if \(input\.value !== \'\' && !input\.checkValidity\(\)\) \{\s*alert\(`Invalid value. Please enter a value between \$\{input\.min\} and \$\{input\.max\}\.`\);\s*input\.focus\(\);\s*return;\s*\}\s*\}\s*\}', replacement, js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
