import firebase_admin
from firebase_admin import credentials, db
import datetime
import os

class FirebaseHandler:
    """Class to handle Firebase Realtime Database connections."""
    def __init__(self, cert_path, db_url, root_node='sensor_data'):
        # Initialize only if not already initialized
        if not firebase_admin._apps:
            if not os.path.exists(cert_path):
                error_msg = f"Error: Certificate file not found at {cert_path}"
                print(error_msg)
                raise FileNotFoundError(error_msg)
                
            cred = credentials.Certificate(cert_path)
            firebase_admin.initialize_app(cred, {'databaseURL': db_url})
            print("[INFO] Firebase Connected")
        
        self.ref = db.reference(root_node)
        self.history_ref = self.ref.child('fall_history')

    def update_status(self, device_name, status):
        """Updates the current status of the device."""
        self.ref.update({
            'device': device_name,
            'status': status,
            'last_update': str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        })

    def log_fall(self):
        """Logs a fall event to history."""
        self.history_ref.push().set({
            'ROOMID' : '301',
            'status': 'Fall Down',
            'timestamp': str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        })
        print("[ALERT] Fall logged to Firebase.")
    
    def get_motion_state(self):
        """
        Reads the current motion state from Firebase.
        Returns: int (0 or 1) or None if error
        ESP32 publishes to /hospital_system/wards/ward_A/room_301/motion path
        """
        try:
            motion_path = "/hospital_system/wards/ward_A/room_301/motion"
            motion_ref = db.reference(motion_path)
            data = motion_ref.get()
            
            # Handle both dictionary and direct value formats
            if data is None:
                return 0  # Default to no motion if data not found
            elif isinstance(data, dict):
                # Data is a dictionary like {'motion': 0}
                if 'val' in data:
                    return int(data['val'])
                return 0
            else:
                # Data is a direct value
                return int(data)
        except Exception as e:
            print(f"[ERROR] Failed to read motion state: {e}")
            return None

if __name__ == "__main__":
    # TEST SECTION
    # This demonstrates how to use the classes together
    
    # 1. Setup Firebase
    cert_path = "Firebase/preserving-fall-detector-firebase-adminsdk-fbsvc-400901162f.json"
    db_url = 'https://preserving-fall-detector-default-rtdb.firebaseio.com/'
    
    fb = FirebaseHandler(cert_path, db_url)
    
    # 2. Setup Analyzer (simulating usage from src.py)
    try:
        from src import FallAnalyzer
        import numpy as np
        
        print("[INFO] Testing FallAnalyzer integration...")
        analyzer = FallAnalyzer()
        
        # Simulate a Fall (Width > Height usually, but here Height < Width for fall? 
        # Wait, in src.py we defined: if aspect_ratio > 1: Standing (Height > Width)
        # So Fall is box_height < box_width (Ratio < 1)
        
        # Create a dummy box that represents a Fall (Wide and Short)
        # Box format: [start_x, start_y, end_x, end_y]
        dummy_fall_box = np.array([100, 100, 300, 200]) # W=200, H=100 -> Ratio 0.5
        
        _, status, _ = analyzer.analyze(dummy_fall_box, 640, 480)
        print(f"Detected Status: {status}")
        
        if status == "Fall Down":
            fb.update_status("ESP32-S3-CAM", "Fall Detected!")
            fb.log_fall()
        else:
            fb.update_status("ESP32-S3-CAM", "Online")
            
    except ImportError:
        print("Could not import src.FallAnalyzer. Make sure src.py is valid.")
    except Exception as e:
        print(f"An error occurred: {e}")