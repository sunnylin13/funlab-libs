# Funlab-Libs - The core libraries for funlab-flaskr

## Installation

pip install funlab-libs

## Reqirements

- python = ">=3.11,<4.0"
- colorama = "^0.4.6"
- sqlalchemy = "^2.0.17"
- cryptography = "^42.0.2"
- pandas = "^2.0.3"


# Flask SSE Application with Dynamic Event Types and Shared JavaScript

## Step-by-Step Plan

1. **Create a Generic JavaScript File**:
    - Define a function to handle the subscription to different `eventType` and update the UI accordingly.

2. **Include the Generic JavaScript File in Each Template**:
    - Include the generic JavaScript file in each HTML template.
    - Call the function defined in the JavaScript file with the appropriate `eventType`.

3. **Define Routes for Each Page**:
    - Create separate routes in Flask for each page.
    - Render different templates for each route.

4. **Subscribe to Different `eventType`**:
    - Use the `/events/<event_type>` endpoint to subscribe to the specific `eventType`.

5. **Handle Events Individually**:
    - Each page will have its own JavaScript code to handle the events.

## Implementation

### 1. Create a Generic JavaScript File (`static/js/sse_handler.js`)

```javascript
function subscribeToEvent(eventType, elementId) {
    const eventSource = new EventSource(`/events/${eventType}`);
    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        const eventDiv = document.createElement('div');
        eventDiv.textContent = `Event Type: ${data.event_type}, Data: ${JSON.stringify(data.payload)}`;
        document.getElementById(elementId).appendChild(eventDiv);
    };
}
```

### 2. Include the Generic JavaScript File in Each Template

Jinja Template for Notifications (templates/notifications.html)
```html
<!DOCTYPE html>
<html>
<head>
    <title>Notifications</title>
    <script src="{{ url_for('static', filename='js/sse_handler.js') }}"></script>
</head>
<body>
    <h1>Notifications</h1>
    <div id="notifications"></div>
    <script>
        subscribeToEvent('notifications', 'notifications');
    </script>
</body>
</html>
```

Jinja Template for Quotes (templates/quotes.html)
```html
<!DOCTYPE html>
<html>
<head>
    <title>Quotes</title>
    <script src="{{ url_for('static', filename='js/sse_handler.js') }}"></script>
</head>
<body>
    <h1>Quotes</h1>
    <div id="quotes"></div>
    <script>
        subscribeToEvent('quotes', 'quotes');
    </script>
</body>
</html>
```

### 3. Updated Flask Application (app.py)
```python
import toml
from flask import Flask, Response, request, render_template
from dataclasses import dataclass
from enum import Enum
from queue import Queue
from threading import Thread
import json
import time

app = Flask(__name__)

# Load the configuration file
config = toml.load('config.toml')

# Extract event types from the configuration
event_types = config['event_types']['types']

# Dynamically create the SSEType enum
SSEType = Enum('SSEType', {event_type.upper(): event_type for event_type in event_types})

# Create event queues dynamically based on the SSEType enum
sse_queues = {event_type: Queue() for event_type in SSEType}

@dataclass
class SSEData:
    event_type: SSEType
    payload: dict  # You can replace with more specific typing

def consume_events(event_type: SSEType, event_queue: Queue):
    while True:
        event = event_queue.get()
        if event is None:  # Exit condition for the thread
            break
        yield f"data: {json.dumps(event.__dict__)}\n\n"

@app.route('/events/<event_type>')
def sse(event_type):
    event_type_enum = SSEType[event_type.upper()]
    return Response(consume_events(event_type_enum, sse_queues[event_type_enum]), content_type='text/event-stream')

@app.route('/publish', methods=['POST'])
def publish_event():
    event_type = SSEType[request.json['event_type'].upper()]
    event = SSEData(event_type=event_type, payload=request.json['data'])
    sse_queues[event_type].put(event)
    return '', 204

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/notifications')
def notifications():
    return render_template('notifications.html')

@app.route('/quotes')
def quotes():
    return render_template('quotes.html')

if __name__ == '__main__':
    app.run(debug=True)
```

## Explanation

### 1.Create a Generic JavaScript File:

- The subscribeToEvent function takes two parameters: eventType and elementId.
- It creates a new EventSource for the given eventType.
- The onmessage event handler processes the incoming events and appends them to the specified element (elementId).

### 2. Include the Generic JavaScript File in Each Template:

- The `<script src="{{ url_for('static', filename='js/sse_handler.js') }}"></script>` line includes the generic JavaScript file in the template.
- The `<script>` block calls the subscribeToEvent function with the appropriate eventType and elementId.

### 3.Define Routes for Each Page:

- The `/notifications` route renders the notifications.html template.
- The `/quotes` route renders the quotes.html template.

### 4.Subscribe to Different eventType:

- In `notifications.html`, the JavaScript code subscribes to the `notifications` event type using `subscribeToEvent('notifications', 'notifications')`.
- In `quotes.html`, the JavaScript code subscribes to the `quotes` event type using `subscribeToEvent('quotes', 'quotes')`.

### 5.Handle Events Individually:

- Each template has its own JavaScript code to handle the events and update the UI accordingly.
- The `onmessage` event handler processes the incoming events and appends them to the respective div (`notifications` or `quotes`).

This setup allows each page to subscribe to different eventType and consume their event data individually and independently. Each page has its own route, template, and JavaScript code to handle the events. The generic JavaScript file is shared across all pages, making the code more maintainable and reusable.