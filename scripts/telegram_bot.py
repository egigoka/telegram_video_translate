import telebot
import requests
import os
import traceback
import inspect
import telegrame
from commands import Console, Str
from secrets import TELEGRAM_TOKEN, ADMIN_CHAT_ID

# Initialize the Telegram bot with your token
BOT = telebot.TeleBot(TELEGRAM_TOKEN)
    

# Function to execute the downloaded script
def run_downloaded_script(arguments, chat_id):
    print("start ", current_function_name())
    script_path = './scripts/downloaded_script.sh'
    console = Console()

    def line_handler(line):
        print("start ", current_function_name())
        try:
            telegrame.send_message(BOT, chat_id=chat_id, text=line)
        except:
            print("Cannot send message:", line)
        print("end ", current_function_name())
    
    result = console.get_output(f"bash {script_path} {arguments}", 
                                print_std=True,
                                hook_stderr=line_handler, 
                                hook_stdout=line_handler)
    print("end ", current_function_name())
    return result

def current_function_name():
    return inspect.stack()[1].function

# Handle /start command
@BOT.message_handler(commands=['start'])
def send_welcome(message):
    print("start ", current_function_name())
    BOT.reply_to(message, "Welcome to the bot! Send me a link to process.")
    print("end ", current_function_name())

# Handle received links
@BOT.message_handler(func=lambda message: True)
def handle_message(message):
    print("start ", current_function_name())
    link = message.text
    chat_id = message.chat.id
    message_id = message.message_id

    if chat_id != ADMIN_CHAT_ID:
        BOT.forward_message(ADMIN_CHAT_ID, chat_id, message_id)

    # Validate the link if needed, then process it
    try:
        telegrame.send_message(BOT, chat_id, "Processing your link...")
        print("run download script start ", current_function_name())
        output = run_downloaded_script(link, chat_id)

        print("analyzing video name ", current_function_name())
        
        video_path = ""
        for line in Str.nl(output):
            print(f"{line=}")
            if not "Filename: " in line:
                print("no 'Filename: '")
                continue
            substring = Str.substring(line, "Filename: ").strip()
            
            try:
                substring = Str.substring(substring, "[1m")
            except KeyError:
                pass
            
            try:
                substring = Str.substring(substring, "", "\x1b[0m")
            except KeyError:
                pass
            
            video_path = substring.strip()
            
        print(f"video name is '{video_path}' ", current_function_name())
        
        if os.path.exists(video_path):
            with open(video_path, 'rb') as video:
                print("sending video ", current_function_name())
                
                for retry in range(20):
                    try:
                        BOT.send_video(chat_id, video)
                    except Exception:
                        
                print("video sent ", current_function_name())
        else:
            current_dir = os.getcwd()
            newline = "\n"
            tree = []
            for root, dirs, files in os.walk("."):
                for dir in dirs:
                    tree.append("dir " + root + os.sep + dir)
                for file in files:
                    tree.append("file " + root + os.sep + file)
            message = "No video generated. \n" \
                    + f"Video path: '{video_path}'\n" \
                    + f"Current dir: '{current_dir}'\n" \
                    + f"Tree: '{newline.join(tree)}'"
            
            print(message, current_function_name())
            BOT.send_message(chat_id, message)
    
    except Exception as e:
        print("end error ", current_function_name())
        error_message = ''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__))
        print(error_message)
        try:
            telegrame.send_message(BOT, chat_id, f"An error occurred: {error_message}")
        except Exception:
            print("Cannot send error message D:")
    print("end ", current_function_name())

# Start polling for messages
BOT.polling()
