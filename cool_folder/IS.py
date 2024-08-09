import subprocess
import json
import google.generativeai as genai
from datetime import datetime
import getpass
import socket
import os
import logging
import time

logging.basicConfig(filename='[YOUR_LOG_FILE_PATH]', 
                    level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s:%(message)s')

grpc_log_file = '[YOUR_GRPC_LOG_FILE_PATH]'
grpc_logger = logging.getLogger('grpc')
grpc_handler = logging.FileHandler(grpc_log_file)
grpc_logger.addHandler(grpc_handler)
grpc_logger.setLevel(logging.WARNING)

GREEN = "\033[92m"
BLUE = "\033[94m"
RESET = "\033[0m"

def setup_ssl_certificates():
    try:
        subprocess.run(["curl", "-Lo", "roots.pem", "https://pki.google.com/roots.pem"], check=True)
        os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = os.path.join(os.getcwd(), "roots.pem")
        logging.info("SSL certificates set up successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error setting up SSL certificates: {e}")
        raise

setup_ssl_certificates()

genai.configure(api_key="[YOUR_API_KEY]")

generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

safety_settings = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_NONE"
    },
]

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings
)

def retry_on_failure(retries=5, delay=1, backoff=2):
    def retry_decorator(func):
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logging.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {current_delay} seconds...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                    attempt += 1
            logging.error("Max retries exceeded. Exiting.")
            raise e
        return wrapper
    return retry_decorator

def load_pilot_prompt():
    try:
        with open('[YOUR_PILOT_PROMPT_FILE_PATH]', 'r', encoding='utf-8') as file:
            pilot_prompt = file.read()
        logging.info("Pilot prompt loaded successfully.")
        return pilot_prompt
    except FileNotFoundError:
        logging.error(f"Error: Pilot prompt not found.")
        return None

pilot_prompt = load_pilot_prompt()
if pilot_prompt:
    chat_session = model.start_chat(history=[{
        "role": "user",
        "parts": [{"text": pilot_prompt}]
    }])

    chat_session.history.append({
        "role": "model",
        "parts": [{"text": "OK, I'm going to follow these instructions exactly. I'm ready to send some commands! And always format my json properly!"}]
    })

def load_memory():
    try:
        with open('[YOUR_MEMORY_FILE_PATH]', 'r', encoding='utf-8') as file:
            memory = json.load(file)
        logging.info("Memory loaded from file.")
        return memory
    except FileNotFoundError:
        logging.warning("No previous memory found, starting fresh.")
        return []

def save_memory(memory):
    with open('[YOUR_MEMORY_FILE_PATH]', 'w', encoding='utf-8') as file:
        json.dump(memory, file, indent=4)
    logging.info("Memory saved to file.")

ai_memory = load_memory()

def store_memory(message):
    ai_memory.append(message)
    save_memory(ai_memory)

def execute_command(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        response = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
        status = "success" if result.returncode == 0 else "error"
        return response, status
    except Exception as e:
        logging.error(f"Error executing command: {command}. Exception: {str(e)}")
        return str(e), "error"

@retry_on_failure()
def interact_with_ai(prompt):
    chat_session.history.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })
    response = chat_session.send_message(prompt)
    return response

def clean_and_parse_json(response_text):
    try:
        response_text = response_text.strip().replace('\\n', '').replace('\\r', '').replace('\n', '').replace('\r', '')
        start_index = response_text.find('{')
        end_index = response_text.rfind('}')
        if (start_index != -1 and end_index != -1):
            response_text = response_text[start_index:end_index + 1]
        return json.loads(response_text)
    except json.JSONDecodeError:
        logging.error("Failed to parse AI response as JSON.")
        return None

def handle_ai_response(ai_response):
    try:
        response_text = ai_response.candidates[0].content.parts[0].text
        ai_json = clean_and_parse_json(response_text)
        if ai_json is None:
            return
        
        print(f"{BLUE}AI Response: {ai_json['response']}{RESET}")
        
        if "command" in ai_json and ai_json["command"] != "none":
            command_result, status = execute_command(ai_json["command"])
            print(f"Command Result: {command_result}")
            
            result_prompt = json.dumps({
                "response": f"The result of the command was: {command_result}",
                "command": ai_json["command"],
                "status": status,
                "next_step": "Awaiting further instructions."
            })
            next_response = interact_with_ai(result_prompt)
            handle_ai_response(next_response)
        else:
            logging.info("Task completed or no further commands to execute.")
    except AttributeError as e:
        logging.error(f"An error occurred: {e}")

def main():
    if not pilot_prompt:
        logging.error("Cannot proceed without the pilot prompt.")
        return

    username = getpass.getuser()
    device_name = socket.gethostname()
    prompt = f"{GREEN}{username}@{device_name} ~ % {RESET}"
    
    while True:
        user_input = input(prompt)
        
        if user_input.lower() in ["exit", "quit"]:
            print("Exiting the session.")
            break
        
        full_prompt = f"Please respond to the following command or request from the user '{username}':\n{user_input}\n"
        
        try:
            ai_response = interact_with_ai(full_prompt)
            handle_ai_response(ai_response)
        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()