from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def get_password_hash(password):
    return pwd_context.hash(password)


with open("input.txt", "r") as input_file, open("students.txt", "w") as output_file:
    for line in input_file:
        parts = line.split()
        if len(parts) >= 2:
            username = parts[1]
            password = username[::-1]
            hashed = get_password_hash(password)
            output_file.write(f"{username}:{hashed}\n")
