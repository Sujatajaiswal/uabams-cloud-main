import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

validation_code = """
    const usernameRegex = /^[A-Za-z0-9_]+$/;
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)(?=.*[@#$%!]).{8,}$/;
    
    if (!id && !usernameRegex.test(username)) {
      alert("Invalid Username. Use only letters, numbers, and underscores (no spaces).");
      return;
    }
    if (password && !passwordRegex.test(password)) {
      alert("Invalid Password. It must be at least 8 characters long, contain at least 1 uppercase letter, 1 lowercase letter, 1 number, and 1 special character (@, #, $, %, !).");
      return;
    }
"""

submit_target = """    const isActive = document.getElementById('userIsActive').checked;"""

js = js.replace(submit_target, submit_target + validation_code)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
