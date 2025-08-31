
from functools import wraps
from flask_login import current_user
from flask import render_template
def role_required(roles:list|tuple):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if getattr(current_user, 'role', '|@_@|') not in roles:
                # Redirect to an unauthorized page or show an error message
                return render_template('error-403.html'), 403
            # Call the protected route handler function
            return func(*args, **kwargs)
        return wrapper
    return decorator

def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, 'is_admin', False):
            # Redirect to an unauthorized page or show an error message
            return render_template('error-403.html'), 403
        # Call the protected route handler function
        return func(*args, **kwargs)
    return wrapper
