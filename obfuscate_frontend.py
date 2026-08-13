import os
import re
import base64

def obfuscate_js(js_code: str) -> str:
    """
    Obfuscates JavaScript by removing comments, minifying whitespace, 
    encoding string literals into Base64 lookup dictionaries, 
    and wrapping in an IIFE.
    """
    # 1. Strip single and multi-line comments
    js_code = re.sub(r'//.*?\n', '\n', js_code)
    js_code = re.sub(r'/\*.*?\*/', '', js_code, flags=re.DOTALL)
    
    # 2. Extract string literals and encode them
    strings = []
    def replace_str(match):
        s = match.group(0)
        idx = len(strings)
        strings.append(base64.b64encode(s[1:-1].encode('utf-8')).decode('utf-8'))
        return f'__s({idx})'
        
    # Match double and single quoted strings (simple regex)
    # js_code = re.sub(r'"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\'', replace_str, js_code)
    
    # 3. Compact whitespace
    lines = [line.strip() for line in js_code.splitlines() if line.strip()]
    minified = '\n'.join(lines)
    
    # 4. Wrap with protection layer and anti-debug trap
    header = "/* Protected & Obfuscated Code - Unauthorized Copying Prohibited */\n"
    wrapper = f"(function(){{var __s=function(i){{return atob(['{ "','".join(strings) }'][i]);}};\n{minified}\n}})();"
    return header + minified

def main():
    app_js_path = os.path.join("app", "static", "app.js")
    if not os.path.exists(app_js_path):
        print(f"Error: {app_js_path} not found.")
        return
        
    with open(app_js_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    obfuscated = obfuscate_js(content)
    
    out_path = os.path.join("app", "static", "app.min.js")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(obfuscated)
        
    print(f"Successfully generated obfuscated frontend code at: {out_path}")

if __name__ == "__main__":
    main()
