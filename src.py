import cv2
import numpy as np
import os
import time
from FireBaseConnect import FirebaseHandler

class VideoSource:
    """Class for handling video input (file or camera)."""
    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            print(f"Error: Could not open video source {source}")

    def read(self):
        return self.cap.read()

    def release(self):
        self.cap.release()

class PersonDetector:
    """Class for detecting persons using MobileNetSSD."""
    def __init__(self, prototxt_path, model_path, confidence_threshold=0.5):
        self.CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
                        "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
                        "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
                        "sofa", "train", "tvmonitor"]
        self.confidence_threshold = confidence_threshold
        
        print(f"[INFO] Loading model from: {model_path}")
        self.net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)

    def detect(self, frame):
        """Returns the detection with the highest confidence for a person."""
        (h, w) = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        detections = self.net.forward()

        max_conf_idx = -1
        max_conf_val = 0

        for i in np.arange(0, detections.shape[2]):
            percent = detections[0, 0, i, 2]
            idx = int(detections[0, 0, i, 1])

            if self.CLASSES[idx] != "person" or percent <= self.confidence_threshold:
                continue

            if percent > max_conf_val:
                max_conf_val = percent
                max_conf_idx = i

        if max_conf_idx != -1:
            return detections[0, 0, max_conf_idx, 3:7] * np.array([w, h, w, h]), max_conf_val
        
        return None, 0

class FallAnalyzer:
    """Class for analyzing detections to determine fall status."""
    def __init__(self, smoothing_alpha=0.4):
        self.prev_box = None
        self.alpha = smoothing_alpha

    def analyze(self, raw_box, frame_width, frame_height):
        """Applies smoothing and determines if the person is falling."""
        current_box = raw_box.astype("int")

        # Smoothing Logic
        if self.prev_box is not None:
            dist = np.linalg.norm(current_box - self.prev_box)
            if dist < 100:  # If distance is small, smooth it
                current_box = (self.alpha * current_box + (1 - self.alpha) * self.prev_box).astype("int")

        self.prev_box = current_box.astype("float")
        
        (startX, startY, endX, endY) = current_box
        box_width = endX - startX
        box_height = endY - startY
        
        # Calculate Aspect Ratio
        if box_width > 0:
            aspect_ratio = box_height / box_width
        else:
            aspect_ratio = 0
            
        # Determine Status
        if aspect_ratio > 1:
            status = "Standing"
        else:
            status = "Fall Down"
        
        return current_box, status, aspect_ratio

class HumanDetectionApp:
    """Main Application Class."""
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.prototxt_path = os.path.join(self.base_dir, "MobileNetFile", "MobileNetSSD.prototxt")
        self.model_path = os.path.join(self.base_dir, "MobileNetFile", "MobileNetSSD.caffemodel")
        self.video_path = os.path.join(self.base_dir, "Video_Testing", "SlipTest.mp4")
        self.firebase_cert_path = os.path.join(self.base_dir, "Firebase", "motionsensorproject-14403-firebase-adminsdk-fbsvc-01373b8bb2.json")
        self.firebase_db_url = 'https://motionsensorproject-14403-default-rtdb.firebaseio.com/'
        
        self.detector = PersonDetector(self.prototxt_path, self.model_path)
        self.analyzer = FallAnalyzer()
        self.video_source = VideoSource(self.video_path)
        self.firebase = FirebaseHandler(self.firebase_cert_path, self.firebase_db_url, root_node='realtime_camera_src')
        
        self.color_green = (0, 255, 0)
        self.color_red = (0, 0, 255)
        
        # Debounce for fall logging (prevent spamming every frame)
        self.last_fall_log_time = 0
        self.fall_log_cooldown = 5.0 # seconds

    def run(self):
        print("[INFO] Starting video stream...")
        while True:
            ret, frame = self.video_source.read()
            if not ret:
                print("Video ended or cannot read frame.")
                break

            # Detection
            raw_box, confidence = self.detector.detect(frame)

            if raw_box is not None:
                (h, w) = frame.shape[:2]
                
                # Analysis
                box, status, ratio = self.analyzer.analyze(raw_box, w, h)
                (startX, startY, endX, endY) = box
                
                # Firebase Update
                self.firebase.update_status("ROOM-01", status)
                
                # Log Fall Event (with cooldown)
                if status == "Fall Down":
                    current_time = time.time()
                    if current_time - self.last_fall_log_time > self.fall_log_cooldown:
                        self.firebase.log_fall()
                        self.last_fall_log_time = current_time
                
                # Visualization
                box_width = endX - startX
                font_scale = max(box_width * 0.003, 0.5)
                
                label = f"Person: Ratio: {ratio:.2f}"
                
                color = self.color_green if status == "Standing" else self.color_red
                
                cv2.rectangle(frame, (startX, startY), (endX, endY), self.color_green, 2)
                cv2.putText(frame, f"{label} {status}", (startX, startY - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 2)

            cv2.imshow("Frame", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        self.video_source.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    app = HumanDetectionApp()
    app.run()