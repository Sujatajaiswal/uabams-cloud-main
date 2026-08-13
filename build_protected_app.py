import os
import re
import base64

def obfuscate_javascript_source(code: str) -> str:
    # 1. Remove comments
    code = re.sub(r'//.*?\n', '\n', code)
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    
    # 2. Extract string literals into an encrypted Base64 array
    string_map = []
    
    def string_replacer(match):
        s = match.group(0)
        if len(s) <= 2:
            return s
        quote = s[0]
        content = s[1:-1]
        if not content:
            return s
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        idx = len(string_map)
        string_map.append(encoded)
        return f'_0x_str({idx})'

    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    code_obfuscated = re.sub(pattern, string_replacer, code)

    # 3. Compact whitespace
    lines = [line.strip() for line in code_obfuscated.splitlines() if line.strip()]
    compact_code = ';'.join(lines)
    compact_code = re.sub(r';+', ';', compact_code)
    
    # 4. Generate the scrambled runtime string dictionary & IIFE wrapper
    joined_b64 = '","'.join(string_map)
    
    header = "/* Protected Production Build - Confidential */\n"
    wrapper = "(function(_0x_arr){\n" \
              "    var _0x_dic = [\"" + joined_b64 + "\"];\n" \
              "    window._0x_str = function(i){\n" \
              "        try {\n" \
              "            return decodeURIComponent(escape(atob(_0x_dic[i])));\n" \
              "        } catch(e) {\n" \
              "            return atob(_0x_dic[i]);\n" \
              "        }\n" \
              "    };\n" \
              "})();\n" \
              "(function(){\n" + compact_code + "\n})();\n"
    return header + wrapper

def main():
    app_js_path = os.path.join("app", "static", "app.js")
    backup_js_path = os.path.join("app", "static", "app.src.js")
    
    if not os.path.exists(app_js_path):
        print("app.js not found!")
        return

    # Keep original clean source as app.src.js for developer reference
    if not os.path.exists(backup_js_path):
        with open(app_js_path, "r", encoding="utf-8") as f_in, open(backup_js_path, "w", encoding="utf-8") as f_out:
            f_out.write(f_in.read())

    with open(backup_js_path, "r", encoding="utf-8") as f:
        raw_code = f.read()

    obfuscated_code = obfuscate_javascript_source(raw_code)

    with open(app_js_path, "w", encoding="utf-8") as f:
        f.write(obfuscated_code)

    print("Successfully obfuscated app.js into scrambled production code!")

if __name__ == "__main__":
    main()
