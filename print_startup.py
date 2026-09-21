with open("app/main.py") as f:
    lines = f.readlines()

in_startup = False
for line in lines:
    if line.startswith("@app.on_event(\"startup\")"):
        in_startup = True
    if in_startup:
        print(line, end="")
        if line.startswith("@app.get"):
            break
