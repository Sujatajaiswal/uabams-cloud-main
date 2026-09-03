import re

with open('app/static/styles.css', 'r', encoding='utf-8') as f:
    css = f.read()

# I will replace `.badge { ... color: white; ... }` with `color: black;`
# But to be safe, I'll just append it to the end of the file.

css += "\n\n/* Override badge text color to black as requested by user */\n"
css += ".badge { color: black !important; }\n"

with open('app/static/styles.css', 'w', encoding='utf-8') as f:
    f.write(css)
