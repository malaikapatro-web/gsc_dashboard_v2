import hmac
import time

import streamlit as st

MAX_ATTEMPTS = 5  # wrong guesses allowed per browser session before a lockout
LOCKOUT_SECONDS = 300


def _expected_password() -> str | None:
    try:
        return str(st.secrets["app_password"]) or None
    except Exception:
        return None


def require_password() -> None:
    """Block everything (including Snowflake access) until the viewer enters the app password.

    The password lives only in Streamlit Secrets (`app_password`). If it is missing the app stays locked.
    """
    if st.session_state.get("_auth_ok"):
        return

    expected = _expected_password()
    st.title("GSC SEO Dashboard")
    if expected is None:
        st.error("This app is locked: no `app_password` is set in the app's Secrets.")
        st.stop()

    locked_for = st.session_state.get("_locked_until", 0) - time.time()
    if locked_for > 0:
        st.error(f"Too many wrong attempts. Try again in {int(locked_for // 60) + 1} minute(s).")
        st.stop()

    st.caption("Enter the password to open the dashboard.")
    with st.form("login", clear_on_submit=True):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open dashboard")

    if submitted:
        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state["_auth_ok"] = True
            st.session_state.pop("_fails", None)
            st.rerun()
        fails = st.session_state.get("_fails", 0) + 1
        st.session_state["_fails"] = fails
        time.sleep(1)  # slow down guessing
        if fails >= MAX_ATTEMPTS:
            st.session_state["_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.session_state["_fails"] = 0
            st.error("Too many wrong attempts. Locked for 5 minutes.")
        else:
            st.error("Incorrect password.")
    st.stop()
