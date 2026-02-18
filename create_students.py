with open("input.txt", "r") as input_file, open("students.txt", "w") as output_file:
    for line in input_file:
        parts = line.split()
        if len(parts) >= 2:
            username = parts[1]
            password = username[::-1]
            output_file.write(f"{username}:{password}\n")
