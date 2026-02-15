import cv2
import numpy as np
import os
import threading
import time
import requests
from FireBaseConnect import FirebaseHandler

# ==========================================
# CONFIGURATION
# ==========================================
ESP32_IP = "192.168.1.51"  # <-- ใส่ IP จริงของบอร์ดตรงนี้
SNAPSHOT_URL = f"http://{ESP32_IP}/capture"

CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
           "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
           "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
           "sofa", "train", "tvmonitor"]

COLOR = (0, 255, 0)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
prototxt_path = os.path.join(BASE_DIR, "MobileNetFile", "MobileNetSSD.prototxt")
model_path = os.path.join(BASE_DIR, "MobileNetFile", "MobileNetSSD.caffemodel")

# ==========================================
# CLASS: Threaded Snapshot Camera (Optimized)
# ==========================================
class ThreadedSnapshotCamera:
    def __init__(self, url):
        self.url = url
        self.frame = None
        self.grabbed = False
        self.started = False
        self.read_lock = threading.Lock()
        
        # ✅ ใช้ Session เพื่อรักษา Connection (ลดภาระ CPU ของ ESP32 ลงอย่างมาก)
        self.session = requests.Session() 

    def start(self):
        if self.started:
            return None
        self.started = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        return self

    def update(self):
        while self.started:
            try:
                # ใช้ session.get แทน requests.get
                response = self.session.get(self.url, timeout=3.0)
                
                if response.status_code == 200:
                    img_array = np.array(bytearray(response.content), dtype=np.uint8)
                    
                    # โหลดภาพสีตามปกติ (MobileNet แม่นยำกว่าในภาพสี)
                    # เปลี่ยนเลข 1 เป็น 0 เพื่อโหลดแบบ Grayscale
                    frame = cv2.imdecode(img_array, 0) 
                    
                    if frame is not None:
                        # Resize เล็กน้อยเพื่อความเร็ว
                        frame = cv2.resize(frame, (400, 300))
                        
                        with self.read_lock:
                            self.frame = frame
                            self.grabbed = True
                
                # ✅ สำคัญมาก: หน่วงเวลาเล็กน้อยเพื่อให้ ESP32 ได้พัก
                # 0.05 = 20 FPS (เพียงพอสำหรับ Fall Detection)
                time.sleep(0.02) 

            except Exception as e:
                # กรณีเชื่อมต่อไม่ได้ ให้รอแป๊บหนึ่งแล้วลองใหม่
                print(f"[Warning] Camera connection issue: {e}")
                self.grabbed = False
                time.sleep(1.0)

    def read(self):
        with self.read_lock:
            frame = self.frame.copy() if self.frame is not None else None
            grabbed = self.grabbed
        return grabbed, frame

    def stop(self):
        self.started = False
        if hasattr(self, 'thread'):
            self.thread.join()

# ==========================================
# STATE MACHINE STATES
# ==========================================
STATE_IDLE = "IDLE"       # Waiting for motion
STATE_ACTIVE = "ACTIVE"   # Motion detected, camera active
STATE_STOPPING = "STOPPING"  # Transitioning to idle

# ==========================================
# MAIN FUNCTION WITH STATE MACHINE
# ==========================================
def main():
    # Initialize Firebase
    cert_path = "Firebase/preserving-fall-detector-firebase-adminsdk-fbsvc-a0baf4193e.json"
    db_url = 'https://preserving-fall-detector-default-rtdb.firebaseio.com/'
    
    try:
        fb = FirebaseHandler(cert_path, db_url)
        print("[INFO] Firebase connected successfully")
    except Exception as e:
        print(f"[ERROR] Firebase Connection Failed: {e}")
        print("[WARNING] Continuing without Firebase integration")
        fb = None

    print(f"[INFO] Loading model...")
    net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)

    # State machine variables
    current_state = STATE_IDLE
    stream = None
    prev_box = None
    last_motion_check = 0
    motion_check_interval = 0.5  # Check Firebase every 0.5 seconds
    
    print("[INFO] System ready. Monitoring Firebase for motion...")
    
    while True:
        try:
            current_time = time.time()
            
            # ==========================================
            # STATE: IDLE - Waiting for motion
            # ==========================================
            if current_state == STATE_IDLE:
                # Check Firebase for motion state periodically
                if current_time - last_motion_check >= motion_check_interval:
                    last_motion_check = current_time
                    
                    if fb:
                        motion_state = fb.get_motion_state()
                        
                        if motion_state == 1:
                            print("[INFO] Motion detected! Starting camera...")
                            current_state = STATE_ACTIVE
                            
                            # Start camera stream
                            stream = ThreadedSnapshotCamera(SNAPSHOT_URL).start()
                            time.sleep(1.0)  # Give camera time to initialize
                            prev_box = None
                    else:
                        # If no Firebase, stay in IDLE (or could default to ACTIVE)
                        print("[WARNING] No Firebase connection, cannot monitor motion")
                        time.sleep(1.0)
                
                # Sleep to reduce CPU usage in idle state
                time.sleep(0.1)
                continue
            
            # ==========================================
            # STATE: ACTIVE - Processing camera feed
            # ==========================================
            elif current_state == STATE_ACTIVE:
                # Check motion state periodically
                if current_time - last_motion_check >= motion_check_interval:
                    last_motion_check = current_time
                    
                    if fb:
                        motion_state = fb.get_motion_state()
                        
                        if motion_state == 0:
                            print("[INFO] Motion ended. Stopping camera...")
                            current_state = STATE_STOPPING
                            continue
                
                # Process camera frame
                if stream:
                    grabbed, frame = stream.read()
                    
                    # ถ้ายังไม่มีภาพ ให้ข้ามลูปไปก่อน (อย่าพึ่งรัน Model)
                    if not grabbed or frame is None:
                        time.sleep(0.01)
                        continue

                    # ตรวจสอบว่าเป็นภาพ Grayscale หรือไม่ ถ้าใช่ให้แปลงเป็น BGR ก่อนเข้าโมเดล
                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                    (h, w) = frame.shape[:2]
                    blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
                    net.setInput(blob)
                    detections = net.forward()
                    
                    max_conf_idx = -1
                    max_conf_val = 0

                    for i in np.arange(0, detections.shape[2]):
                        percent = detections[0, 0, i, 2]
                        idx = int(detections[0, 0, i, 1])
                        
                        # กรองเอาเฉพาะ Person (15)
                        if CLASSES[idx] != "person" or percent <= 0.5:
                            continue
                        
                        if percent > max_conf_val:
                            max_conf_val = percent
                            max_conf_idx = i

                    if max_conf_idx != -1:
                        box = detections[0, 0, max_conf_idx, 3:7] * np.array([w, h, w, h])
                        current_box = box.astype("int")

                        if prev_box is not None:
                            dist = np.linalg.norm(current_box - prev_box)
                            if dist < 100:
                                alpha = 0.4
                                current_box = (alpha * current_box + (1 - alpha) * prev_box).astype("int")
                        
                        prev_box = current_box.astype("float")
                        (startX, startY, endX, endY) = current_box
                        box_width = endX - startX
                        box_height = endY - startY
                        aspect_ratio = box_height / box_width if box_width > 0 else 0
                        
                        label = "{}: Ratio: {:.2f}".format("Person", aspect_ratio)
                        cv2.rectangle(frame, (startX, startY), (endX, endY), COLOR, 2)
                        font_scale = max(box_width * 0.003, 0.5)

                        if aspect_ratio > 1:
                            status_text = " Standing"
                            text_color = COLOR
                        else:
                            status_text = " Fall Down"
                            text_color = (0, 0, 255)

                        cv2.putText(frame, label + status_text, (startX, startY - 5), 
                                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_color, 2)

                        # Firebase Update Logic
                        if fb:
                            try:
                                # Update status to Firebase
                                fb.update_status("ESP32-S3-CAM", status_text.strip())
                                
                                # Optional: Log fall history if fall detected
                                if "Fall" in status_text:
                                    # fb.log_fall() # Uncomment if history logging is desired
                                    pass

                            except Exception as e:
                                print(f"[ERROR] Firebase Update: {e}")

                    # Display state indicator
                    cv2.putText(frame, f"State: {current_state}", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                    
                    cv2.imshow("ESP32 Fall Detection", frame)
                    
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
            
            # ==========================================
            # STATE: STOPPING - Clean up camera
            # ==========================================
            elif current_state == STATE_STOPPING:
                if stream:
                    stream.stop()
                    stream = None
                    print("[INFO] Camera stopped")
                
                cv2.destroyAllWindows()
                current_state = STATE_IDLE
                prev_box = None
                print("[INFO] Returning to IDLE state. Monitoring for motion...")
        
        except KeyboardInterrupt:
            print("\n[INFO] Keyboard interrupt received. Shutting down...")
            break
        except Exception as e:
            print(f"[ERROR] Main Loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1.0)
    
    # Cleanup
    if stream:
        stream.stop()
    cv2.destroyAllWindows()
    print("[INFO] System shutdown complete")

if __name__ == "__main__":
    main()