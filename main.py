import telebot
from telebot.types import Message
import fitz  # PyMuPDF
from PIL import Image
from io import BytesIO
import os
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
import zipfile
import rarfile
import shutil
import py7zr
from webserver import keep_alive

telegram_token = os.environ['Bot_token']
bot = telebot.TeleBot(telegram_token)

# Dictionary to store user session data (img2pdf)
user_images = {}
user_pdf_name = {}

# Dictionary to store user settings (image resize)
user_settings = {}

# Dictionary to track the processing status for each chat (pdf splitter)
processing_status = {}

# Pdf merger objects
pdfs_received = []
pdfs_received_messages = []
progress_message = None
merge_in_progress = False


# handle help command
@bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = """
This bot can perform various operations with PDF files and images.

<b>PDF Operations:</b>
/mergepdf - Merge multiple PDF files into a single PDF.\n

/splitpdf - Split a PDF file into individual pages.
    Reply to a PDF file with the '/splitpdf' command.\n

<b>Image Operations:</b>
/resizeimage - Resize an image.\n

<b>Image to pdf:</b>
/image2pdf - convert images to pdf.\n

<b>Archive Operations:</b>
/unarchive - Unarchive a compressed file (zip, rar, 7z).

"""
    bot.reply_to(message, help_text, parse_mode="HTML")

# handle mergerpdf command
@bot.message_handler(commands=['mergepdf'])
def handle_mergepdf(message):
    global pdfs_received, pdfs_received_messages, progress_message, merge_in_progress
    pdfs_received = []
    pdfs_received_messages = []
    progress_message = None
    merge_in_progress = True
    bot.reply_to(message, "Please send the PDFs one by one. Send '`DONE`' when finished.", parse_mode="Markdown")


@bot.message_handler(content_types=['document'], func=lambda message: message.document.mime_type == 'application/pdf')
def handle_pdf(message):
    global pdfs_received, pdfs_received_messages, progress_message, merge_in_progress
    if merge_in_progress and message.document.mime_type == 'application/pdf':
        file_size = message.document.file_size
        if file_size > 5 * 1024 * 1024:
            bot.reply_to(message, "File size exceeds the limit of 5 MB")
            return

        if len(pdfs_received) >= 5:
            bot.reply_to(message, "Maximum file limit of 5 reached. Please send 'done' to start merging.")
            return

        pdfs_received.append((message.document.file_id, file_size))
        count = len(pdfs_received)
        if len(pdfs_received_messages) > 0:
            try:
                bot.delete_message(message.chat.id, pdfs_received_messages[-1].message_id)
                pdfs_received_messages.pop()
            except telebot.apihelper.ApiTelegramException:
                pass
        pdfs_received_messages.append(bot.reply_to(message, f"{count} PDFs received so far. Please send '`DONE`' when finished.", parse_mode="Markdown"))

@bot.message_handler(func=lambda message: message.text.lower() == 'done')
def handle_merge(message):
    global pdfs_received, pdfs_received_messages, progress_message, merge_in_progress
    if merge_in_progress:
        merge_in_progress = False
        merger = PdfMerger()

        if len(pdfs_received) == 0:
            bot.reply_to(message, "No PDFs received. Send the PDFs first.")
            return

        total_size = sum(size for _, size in pdfs_received)
        if total_size > 15 * 1024 * 1024:
            bot.reply_to(message, "Total file size exceeds the limit of 15 MB. Please send smaller PDFs.")
            return

        for msg in pdfs_received_messages:
            try:
                bot.delete_message(message.chat.id, msg.message_id)
            except telebot.apihelper.ApiTelegramException:
                pass

        progress_message = bot.reply_to(message, "Merging in progress...")

        for index, (file_id, _) in enumerate(pdfs_received):
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_path = os.path.join('merged_pdfs', f"file_{index}.pdf")

            with open(file_path, 'wb') as f:
                f.write(downloaded_file)

            merger.append(file_path)

        merged_file_path = get_unique_file_path('merged_pdfs/merged.pdf')
        merger.write(merged_file_path)
        merger.close()

        try:
            with open(merged_file_path, 'rb') as f:
                bot.send_document(message.chat.id, f)

            merged_count = len(pdfs_received)
            bot.reply_to(message, f"Merging completed. {merged_count} PDFs merged.")

        except Exception as e:
            bot.reply_to(message, "Failed to send the merged PDF.")

        for index in range(len(pdfs_received)):
            file_path = os.path.join('merged_pdfs', f"file_{index}.pdf")
            if os.path.exists(file_path):
                os.remove(file_path)

        if os.path.exists(merged_file_path):
            os.remove(merged_file_path)

        try:
            bot.delete_message(message.chat.id, progress_message.message_id)
        except telebot.apihelper.ApiTelegramException:
            pass

        pdfs_received = []
        pdfs_received_messages = []

        # Remove the 'merged_pdfs' directory
        shutil.rmtree('merged_pdfs')

    else:
        bot.reply_to(message, "Invalid command. Send '/help' for more information.")

def get_unique_file_path(file_path):
    base_dir = os.path.dirname(file_path)
    base_name, ext = os.path.splitext(os.path.basename(file_path))
    suffix = 1
    while os.path.exists(file_path):
        file_path = os.path.join(base_dir, f"{base_name}_{suffix}{ext}")
        suffix += 1
    return file_path


if not os.path.exists('merged_pdfs'):
    os.makedirs('merged_pdfs')


# Directory for operations
WORK_DIR = "PDF2IMG"
os.makedirs(WORK_DIR, exist_ok=True)

@bot.message_handler(commands=['pdf2image'])
def pdf2image_command(message: Message):
    if message.reply_to_message and message.reply_to_message.document:
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        file_path = file_info.file_path
        file_extension = message.reply_to_message.document.file_name.split('.')[-1].lower()

        if file_extension != 'pdf':
            bot.reply_to(message, "The file you replied to is not a PDF. Please reply to a valid PDF file.")
            return

        # Download the PDF file into the working directory
        file_name = os.path.join(WORK_DIR, f"{message.reply_to_message.document.file_id}.pdf")
        downloaded_file = bot.download_file(file_path)
        with open(file_name, 'wb') as pdf_file:
            pdf_file.write(downloaded_file)

        bot.reply_to(message, "Converting PDF to images. Please wait...")

        try:
            # Open the PDF using PyMuPDF
            pdf_document = fitz.open(file_name)
            image_files = []

            for page_number in range(len(pdf_document)):
                page = pdf_document[page_number]

                # Use a very high DPI for rendering to closely match the original quality
                zoom_x = 4  # Horizontal zoom (higher = better quality)
                zoom_y = 4  # Vertical zoom
                matrix = fitz.Matrix(zoom_x, zoom_y)
                pix = page.get_pixmap(matrix=matrix)

                image_file_name = os.path.join(WORK_DIR, f"page_{page_number + 1}.png")
                pix.save(image_file_name)
                image_files.append(image_file_name)

            pdf_document.close()

            # Send images back as documents
            for image_file in image_files:
                with open(image_file, 'rb') as img:
                    bot.send_document(message.chat.id, img)

            bot.reply_to(message, f"Conversion completed! {len(image_files)} pages sent as documents.")

        except Exception as e:
            bot.reply_to(message, f"An error occurred: {str(e)}")

        finally:
            # Cleanup temporary files
            for file in os.listdir(WORK_DIR):
                file_path = os.path.join(WORK_DIR, file)
                if os.path.exists(file_path):
                    os.remove(file_path)
    else:
        bot.reply_to(message, "Please reply to an already uploaded PDF file with this command.")    
    

# Define a handler for the /unarchive command
@bot.message_handler(commands=['unarchive'])
def handle_unarchive_command(message):
    bot.reply_to(message, "Please upload a .zip, .rar, or .7z file to unarchive.")
# Define a handler for messages containing documents
@bot.message_handler(content_types=['document'], func=lambda message: message.document.mime_type != 'application/pdf')
def handle_document(message):
    file_name = message.document.file_name
    if file_name.endswith('.zip') or file_name.endswith('.rar') or file_name.endswith('.7z'):
        try:
            # Send acknowledgment message
            bot.send_message(message.chat.id, "File received. Extracting...")

            # Download the document (compressed file)
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)

            # Save the downloaded file
            with open(file_name, 'wb') as new_file:
                new_file.write(downloaded_file)

            # Determine the type of compressed file and extract accordingly
            if file_name.endswith('.zip'):
                destination_dir = os.path.join(os.getcwd(), 'extracted_files_zip')
                os.makedirs(destination_dir, exist_ok=True)
                unzip_file(file_name, destination_dir)
            elif file_name.endswith('.rar'):
                destination_dir = os.path.join(os.getcwd(), 'extracted_files_rar')
                os.makedirs(destination_dir, exist_ok=True)
                unrar_file(file_name, destination_dir)
            elif file_name.endswith('.7z'):
                destination_dir = os.path.join(os.getcwd(), 'extracted_files_7z')
                os.makedirs(destination_dir, exist_ok=True)
                un7z_file(file_name, destination_dir)

            # Iterate over subdirectories and send files sequentially
            for subdir, _, _ in os.walk(destination_dir):
                if subdir != destination_dir:
                    bot.send_message(
                        message.chat.id,
                        f"Files in {os.path.relpath(subdir, destination_dir)}:")
                    send_files_in_directory(bot, message.chat.id, subdir)

            # Send completion message
            bot.send_message(message.chat.id, "Extraction complete.")

            # Clean up: delete the downloaded and extracted files
            os.remove(file_name)
            shutil.rmtree(destination_dir)

        except ValueError as e:
            bot.reply_to(message, f"Error: {e}")
        except Exception as e:
            bot.reply_to(message, f"An error occurred: {e}")
            os.remove(file_name)  # Clean up: delete the downloaded file

# Function to handle unzip operation
def unzip_file(file_path, destination_dir):
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(destination_dir)
    except zipfile.BadZipFile:
        os.remove(file_path)
        raise ValueError("The provided ZIP file is corrupted.")


# Function to handle unrar operation
def unrar_file(file_path, destination_dir):
    try:
        with rarfile.RarFile(file_path, 'r') as rar_ref:
            rar_ref.extractall(destination_dir)
    except rarfile.BadRarFile:
        os.remove(file_path)
        raise ValueError("The provided RAR file is corrupted.")


# Function to handle un7z operation
def un7z_file(file_path, destination_dir):
    try:
        with py7zr.SevenZipFile(file_path, mode='r') as archive:
            archive.extractall(destination_dir)
    except py7zr.exceptions.Bad7zFile:
        os.remove(file_path)
        raise ValueError("The provided 7z file is corrupted.")


# Function to send file to the user
def send_file(bot, chat_id, file_path):
    with open(file_path, 'rb') as file:
        bot.send_document(chat_id, file)


# Function to send files in a directory to the user
def send_files_in_directory(bot, chat_id, directory_path):
    files = [
        f for f in os.listdir(directory_path)
        if os.path.isfile(os.path.join(directory_path, f))
    ]
    for file in files:
        send_file(bot, chat_id, os.path.join(directory_path, file))




# handle splitpdf command
@bot.message_handler(commands=['splitpdf'])
def handle_split_pdf(message):
    chat_id = message.chat.id

    # Check if a document is replied to
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.send_message(chat_id, "Please reply to a PDF file with /splitpdf command.")
        return

    # Get file information
    replied_document = message.reply_to_message.document
    file_id = replied_document.file_id
    file_size = replied_document.file_size
    file_name = replied_document.file_name

    # Check if the file has a .pdf extension
    if not file_name.lower().endswith('.pdf'):
        bot.send_message(chat_id, "Invalid file format. Please send a PDF file.")
        return

    if file_size > 20000000:  # 20 MB
        bot.send_message(chat_id, "Sorry, the maximum file size allowed is 20 MB.")
        return

    if chat_id in processing_status and processing_status[chat_id]:
        bot.send_message(chat_id, "Sorry, another PDF file is currently being processed. Please wait for the current process to complete.")
        return

    # Set the processing status for the current chat to True
    processing_status[chat_id] = True

    bot.send_message(chat_id, "PDF file received. Splitting process started...")

    # Download the PDF file
    file_info = bot.get_file(file_id)
    file_path = file_info.file_path
    downloaded_file = bot.download_file(file_path)

    # Save the PDF file locally
    pdf_path = 'temp.pdf'
    with open(pdf_path, 'wb') as f:
        f.write(downloaded_file)

    # Split the PDF into individual pages
    pages = split_pdf_pages(pdf_path)

    # Send each page as a separate file
    for i, page in enumerate(pages):
        page_name = f'page_{i + 1}.pdf'
        with open(page_name, 'wb') as f:
            page.write(f)
        with open(page_name, 'rb') as f:
            bot.send_document(chat_id, f)

        # Remove the generated page file
        os.remove(page_name)

    # Remove the downloaded PDF file
    os.remove(pdf_path)

    # Set the processing status for the current chat to False
    processing_status[chat_id] = False

    bot.send_message(chat_id, "Splitting process completed.")

def split_pdf_pages(file_path):
    input_pdf = PdfReader(file_path)
    pages = []
    for i in range(len(input_pdf.pages)):
        output = PdfWriter()
        output.add_page(input_pdf.pages[i])
        pages.append(output)
    return pages
    

# Handler for Images to PDF /image2pdf
@bot.message_handler(commands=['image2pdf'])
def start_image_to_pdf(message):
    chat_id = message.chat.id
    user_images[chat_id] = []  # Initialize an empty list for storing images
    bot.send_message(
        chat_id,
        "Send the images you want to convert to PDF.\nWhen you're done, type '`go`'.",
        parse_mode='Markdown'
    )

@bot.message_handler(content_types=['photo', 'document'])
def handle_image(message):
    chat_id = message.chat.id

    if chat_id not in user_images:
        bot.send_message(chat_id, "Please start by typing /image2pdf.")
        return

    if message.content_type == 'photo':
        # Handle photos (compressed by Telegram)
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Generate a unique filename for the photo
        filename = f"{chat_id}_{len(user_images[chat_id])}.jpg"
        with open(filename, 'wb') as new_file:
            new_file.write(downloaded_file)

        user_images[chat_id].append(filename)
        bot.send_message(
            chat_id,
            f"Received photo {len(user_images[chat_id])}. Send more or type '`go`'.",
            parse_mode='Markdown'
        )

    elif message.content_type == 'document':
        # Handle documents (uncompressed)
        document = message.document

        # Check if the document MIME type is an image
        if document.mime_type.startswith('image/'):
            file_info = bot.get_file(document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)

            # Use the original file extension if available
            ext = document.file_name.split('.')[-1].lower()
            if ext not in ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff']:
                bot.send_message(chat_id, "Unsupported image format. Please upload JPG, PNG, or similar.")
                return

            # Generate a unique filename for the document
            filename = f"{chat_id}_{len(user_images[chat_id])}.{ext}"
            with open(filename, 'wb') as new_file:
                new_file.write(downloaded_file)

            user_images[chat_id].append(filename)
            bot.send_message(
                chat_id,
                f"Received image document {len(user_images[chat_id])}. Send more or type '`go`'.",
                parse_mode='Markdown'
            )
        else:
            bot.send_message(chat_id, "The uploaded document is not a valid image. Please send JPG, PNG, or similar.")

    else:
        bot.send_message(chat_id, "Unsupported file type. Please send images only.")


@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'go')
def ask_pdf_name(message):
    chat_id = message.chat.id
    if chat_id not in user_images or len(user_images[chat_id]) == 0:
        bot.send_message(chat_id, "You haven't sent any images yet.")
        return

    bot.send_message(
        chat_id,
        "Please send a name for your PDF file. If you want to skip, click /skip."
    )

@bot.message_handler(func=lambda message: message.chat.id in user_images and message.text and message.text != '/skip')
def set_pdf_name(message):
    chat_id = message.chat.id
    user_pdf_name[chat_id] = message.text.strip() + ".pdf"  # Save the user's custom name
    create_pdf(message)

@bot.message_handler(func=lambda message: message.text and message.text.lower() == '/skip')
def skip_pdf_name(message):
    chat_id = message.chat.id
    user_pdf_name[chat_id] = "images.pdf"  # Default PDF name
    create_pdf(message)

def create_pdf(message):
    chat_id = message.chat.id
    if chat_id not in user_images or len(user_images[chat_id]) == 0:
        bot.send_message(chat_id, "You haven't sent any images yet.")
        return

    pdf_filename = user_pdf_name.get(chat_id, "images.pdf")
    images = [Image.open(img).convert('RGB') for img in user_images[chat_id]]
    total_pages = len(images)  # Count the number of images

    # Save images to a single PDF
    images[0].save(pdf_filename, save_all=True, append_images=images[1:])

    # Send the PDF to the user
    with open(pdf_filename, 'rb') as pdf_file:
        bot.send_document(chat_id, pdf_file)

    # Cleanup
    for img in user_images[chat_id]:
        os.remove(img)
    os.remove(pdf_filename)
    del user_images[chat_id]  # Clear the user's session data
    del user_pdf_name[chat_id]  # Clear the user's PDF name

    bot.send_message(
        chat_id,
        f"Your PDF has been created and sent! It contains {total_pages} pages."
    )
    

# Handler for the /resizeimage command
@bot.message_handler(commands=['resizeimage'])
def handle_resize_image_command(message):
    chat_id = message.chat.id

    # Check if the command is used as a reply to a message with an image
    if not message.reply_to_message or not message.reply_to_message.photo:
        bot.reply_to(message, "Please reply to an image with the /resizeimage command.")
        return

    # Retrieve the photo ID from the replied-to message
    photo_id = message.reply_to_message.photo[-1].file_id

    # Download the photo using the photo ID
    file_info = bot.get_file(photo_id)
    downloaded_file = bot.download_file(file_info.file_path)

    # Load the downloaded photo into Pillow
    image = Image.open(BytesIO(downloaded_file))

    # Store the image in user settings
    user_settings[chat_id] = {
        'command_state': 'choose_modification',
        'image': image
    }

    # Get image details
    image_details = f"Image Details:\n\n" \
                    f"File Name: {file_info.file_path}\n" \
                    f"File Size: {file_info.file_size / (1024 * 1024):.2f} MB " \
                    f"({file_info.file_size / 1024:.2f} KB)\n" \
                    f"Image Width: {image.width}px\n" \
                    f"Image Height: {image.height}px\n"

    # Ask the user for the desired modification
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(telebot.types.InlineKeyboardButton('Modify File Size', callback_data='modify_file_size'))
    markup.row(telebot.types.InlineKeyboardButton('Modify File Dimensions', callback_data='modify_file_dimensions'))

    bot.reply_to(message.reply_to_message, f"{image_details}\n"
                                           f"Please choose the modification option:", reply_markup=markup)


# Handler for inline keyboard button callbacks
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id

    if chat_id in user_settings and user_settings[chat_id]['command_state'] == 'choose_modification':
        action = call.data

        if action == 'modify_file_size':
            # Ask the user to enter the desired file size
            bot.reply_to(call.message, "Please enter the desired file size in kilobytes (KB):")
            user_settings[chat_id]['command_state'] = 'enter_file_size'

        elif action == 'modify_file_dimensions':
            # Ask the user to enter the desired dimensions
            bot.reply_to(call.message, "Please enter the desired width and height in pixels (separated by a space):")
            user_settings[chat_id]['command_state'] = 'enter_dimensions'


# Handler for receiving text messages
@bot.message_handler(func=lambda message: message.content_type == 'text')
def handle_text(message):
    chat_id = message.chat.id

    # Check if the user has a command state
    if chat_id in user_settings:
        # Check the command state for the user
        if user_settings[chat_id]['command_state'] == 'enter_file_size':
            try:
                # Get the user's desired file size
                target_file_size = float(message.text.strip())

                # Retrieve the image from user settings
                image = user_settings[chat_id]['image']

                # Reduce the image quality to achieve the target file size
                quality = 80  # Initial quality level
                while True:
                    output = BytesIO()
                    image.save(output, format='JPEG', quality=quality)
                    image_size = output.tell()
                    if image_size / 1024 <= target_file_size:
                        break
                    quality -= 5  # Adjust the decrement value as needed

                # Save the resized image to a temporary file
                output.seek(0)
                with open('resized_image.jpg', 'wb') as f:
                    f.write(output.read())

                # Send the resized image back to the user
                with open('resized_image.jpg', 'rb') as f:
                    bot.send_photo(chat_id, f)

                # Get the details of the resized image
                resized_image_details = f"Resized Image Details:\n\n" \
                                        f"File Name: resized_image.jpg\n" \
                                        f"File Size: {os.path.getsize('resized_image.jpg') / 1024:.2f} KB\n" \
                                        f"Image Width: {Image.open('resized_image.jpg').size[0]}px\n" \
                                        f"Image Height: {Image.open('resized_image.jpg').size[1]}px\n"

                bot.send_message(chat_id, resized_image_details)

                # Clean up the temporary file
                os.remove('resized_image.jpg')

            except ValueError:
                bot.reply_to(message, "Invalid file size. Please enter a valid size in kilobytes (KB).")

            # Clear user settings
            del user_settings[chat_id]

        elif user_settings[chat_id]['command_state'] == 'enter_dimensions':
            try:
                # Get the user's desired dimensions
                dimensions = message.text.strip().split(' ')
                width = int(dimensions[0])
                height = int(dimensions[1])

                # Retrieve the image from user settings
                image = user_settings[chat_id]['image']

                # Resize the image to the desired dimensions
                image.thumbnail((width, height), Image.LANCZOS)

                # Save the resized image to a temporary file
                output_path = 'resized_image.jpg'
                image.save(output_path)

                # Send the resized image back to the user
                with open(output_path, 'rb') as file:
                    bot.send_photo(chat_id, file)

                # Get the details of the resized image
                resized_image_details = f"Resized Image Details:\n\n" \
                                        f"File Name: resized_image.jpg\n" \
                                        f"File Size: {os.path.getsize(output_path) / 1024:.2f} KB\n" \
                                        f"Image Width: {image.width}px\n" \
                                        f"Image Height: {image.height}px\n"

                bot.send_message(chat_id, resized_image_details)

                # Clean up the temporary file
                os.remove(output_path)

            except (IndexError, ValueError):
                bot.reply_to(message, "Invalid dimensions. Please enter valid width and height values.")

            # Clear user settings
            del user_settings[chat_id]

        else:
            bot.reply_to(message, "Invalid command or input.")


    
# Start the bot
keep_alive()
bot.polling(none_stop=True, timeout=123)
