#include <Servo.h>

// Example hardware wiring (HC-SR04 + servo):
// Servo signal -> D9
// HC-SR04 trig -> D10
// HC-SR04 echo -> D11

const int SERVO_PIN = 9;
const int TRIG_PIN = 10;
const int ECHO_PIN = 11;

Servo scanner;

int angle = 0;
int stepDeg = 2;
unsigned long lastDetectionMs = 0;
float emaFrequency = 0.0f;

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return 400.0f;
  }

  return (duration * 0.0343f) / 2.0f;
}

void setup() {
  Serial.begin(9600);
  scanner.attach(SERVO_PIN);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
}

void loop() {
  scanner.write(angle);
  delay(20);

  float distance = readDistanceCm();
  unsigned long now = millis();

  float instantFrequency = 0.0f;
  if (distance >= 2.0f && distance <= 350.0f) {
    if (lastDetectionMs > 0) {
      float deltaSec = (now - lastDetectionMs) / 1000.0f;
      if (deltaSec > 0.0f) {
        instantFrequency = 1.0f / deltaSec;
      }
    }
    lastDetectionMs = now;
  }

  emaFrequency = (0.85f * emaFrequency) + (0.15f * instantFrequency);

  // Output format expected by the Flask app:
  // angle,distance,frequency
  Serial.print(angle);
  Serial.print(",");
  Serial.print(distance, 2);
  Serial.print(",");
  Serial.println(emaFrequency * 10.0f, 2);

  angle += stepDeg;
  if (angle >= 180 || angle <= 0) {
    stepDeg = -stepDeg;
  }

  delay(35);
}
