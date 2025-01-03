import os
import cv2
import numpy as np
from flask import Flask, render_template, Response, request, redirect, url_for
from cvzone.HandTrackingModule import HandDetector
from pptx import Presentation
import win32com.client
import pythoncom

# Initialize Flask App
app = Flask(__name__)

# Paths
UPLOAD_FOLDER = 'static/uploads'
SLIDES_FOLDER = 'static/slides'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SLIDES_FOLDER, exist_ok=True)

# Webcam dimensions
WEBCAM_WIDTH, WEBCAM_HEIGHT = 1280, 720

# Initialize webcam
cap = cv2.VideoCapture(0)
cap.set(3, WEBCAM_WIDTH)
cap.set(4, WEBCAM_HEIGHT)

# Global variables
img_number = 0
slides = []
annotations = [[]]
annotations_number = -1
annotation_start = False
button_press = False
counter = 0
button_delay = 15
gesture_threshold = 400

# Initialize hand detector
detector = HandDetector(detectionCon=0.7, maxHands=1)

def convert_pptx_to_images(pptx_path, output_folder):
    try:
        pythoncom.CoInitialize()
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        pptx_path = os.path.abspath(pptx_path)
        output_folder = os.path.abspath(output_folder)
        presentation = powerpoint.Presentations.Open(pptx_path, ReadOnly=True)
        
        os.makedirs(output_folder, exist_ok=True)
        total_slides = presentation.Slides.Count
        for i in range(1, total_slides + 1):
            slide_path = os.path.join(output_folder, f"slide_{i:03d}.png")
            slide = presentation.Slides(i)
            slide.Export(slide_path, "PNG", 1920, 1080)
        
        presentation.Close()
        powerpoint.Quit()
        
    except Exception as e:
        print(f"Error converting presentation: {str(e)}")
        raise
        
    finally:
        pythoncom.CoUninitialize()

def load_slides():
    slides_list = []
    path_images = sorted(os.listdir(SLIDES_FOLDER), key=len)
    for img_file in path_images:
        if img_file.endswith(('.jpeg', '.jpg', '.png')):
            img_path = os.path.join(SLIDES_FOLDER, img_file)
            img = cv2.imread(img_path)
            if img is not None:
                slides_list.append(img)
            else:
                print(f"Failed to load image: {img_path}")
    return slides_list

def detect_gesture(hand, current_img_number):
    global annotations, annotations_number, annotation_start, button_press
    fingers = detector.fingersUp(hand)
    cx, cy = hand['center']

    # Previous slide - Pinky Up
    if fingers == [0, 0, 0, 0, 1] and current_img_number > 0:
        current_img_number -= 1
        button_press = True

    # Next slide - Thumb up
    elif fingers == [1, 0, 0, 0, 0] and current_img_number < len(slides) - 1:
        current_img_number += 1
        button_press = True

    # Drawing mode - Index finger up
    elif fingers == [0, 1, 0, 0, 0]:
        if not annotation_start:
            annotation_start = True
            annotations_number += 1
            annotations.append([])
        annotations[annotations_number].append((cx, cy))

    # Clear last annotation - Three fingers up
    elif fingers == [0, 1, 1, 1, 0] and annotations:
        annotations.pop()
        annotations_number -= 1

    return current_img_number

def gen_webcam_feed():
    global img_number, button_press, counter, annotation_start

    while True:
        success, img = cap.read()
        if not success:
            break
            
        img = cv2.flip(img, 1)  # Flip webcam feed horizontally

        # Detect hands
        hands, img = detector.findHands(img, flipType=False)

        if hands and not button_press:
            hand = hands[0]
            img_number = detect_gesture(hand, img_number)
        else:
            annotation_start = False

        # Button press delay
        if button_press:
            counter += 1
            if counter > button_delay:
                counter = 0
                button_press = False

        # Encode frame for streaming
        ret, buffer = cv2.imencode('.jpg', img)
        if not ret:
            continue
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

def gen_slide_feed():
    global img_number
    while True:
        if slides and 0 <= img_number < len(slides):
            slide = slides[img_number].copy()

            # Draw annotations
            for annotation_group in annotations:
                if len(annotation_group) > 1:
                    for j in range(len(annotation_group) - 1):
                        cv2.line(slide, annotation_group[j], annotation_group[j + 1], 
                               (0, 0, 200), 12)

            # Encode slide for streaming
            ret, buffer = cv2.imencode('.jpg', slide)
            if not ret:
                continue
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
@app.route('/get_current_slide', methods=['GET'])
def get_current_slide():
    return {'current_slide': img_number + 1, 'total_slides': len(slides)}

@app.route('/')
def index():
    total_slides = len(slides) if slides else 1
    current_slide = img_number + 1 if slides else 1
    return render_template('index.html', total_slides=total_slides, current_slide=current_slide)

@app.route('/webcam_feed')
def webcam_feed():
    return Response(gen_webcam_feed(), 
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/slide_feed')
def slide_feed():
    return Response(gen_slide_feed(), 
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/upload_file', methods=['POST'])
def upload_file():
    try:
        file = request.files.get('file')
        if not file:
            return 'No file uploaded', 400
            
        if not file.filename.endswith('.pptx'):
            return 'Only .pptx files are supported', 400
            
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)

        # Clear existing slides
        for file_name in os.listdir(SLIDES_FOLDER):
            file_path = os.path.join(SLIDES_FOLDER, file_name)
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error removing {file_path}: {str(e)}")
                
        # Convert PPTX to images
        convert_pptx_to_images(filepath, SLIDES_FOLDER)
        
        global slides, img_number, annotations, annotations_number
        slides = load_slides()
        img_number = 0  # Reset to first slide
        annotations = [[]]  # Clear annotations
        annotations_number = -1
        
        if not slides:
            return 'Failed to convert slides', 500
            
        return redirect(url_for('index'))
        
    except Exception as e:
        return f'Error processing file: {str(e)}', 500

@app.route('/previous_slide', methods=['POST'])
def previous_slide():
    global img_number
    if img_number > 0:
        img_number -= 1
    return '', 204

@app.route('/next_slide', methods=['POST'])
def next_slide():
    global img_number
    if img_number < len(slides) - 1:
        img_number += 1
    return '', 204

def cleanup():
    global cap
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()

import atexit
atexit.register(cleanup)

if __name__ == "__main__":
    # Load any existing slides
    slides = load_slides()
    app.run(debug=True)