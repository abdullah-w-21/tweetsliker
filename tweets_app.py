import streamlit as st
import tweepy
import logging
import time
import os
import toml
import json

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Function to load configuration from .streamlit/secrets.toml
def load_secrets():
    try:
        # Streamlit automatically loads secrets.toml into st.secrets
        if st.secrets:
            return st.secrets
        else:
            st.error("No secrets found. Please create .streamlit/secrets.toml")
            return None
    except Exception as e:
        st.error(f"Error loading secrets: {str(e)}")
        return None


# Function to check password
def check_password(secrets):
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == secrets['app']['password']:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password incorrect, show input + error
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct
        return True


# Function to authenticate with Twitter API
def authenticate_twitter(bearer_token, api_key, api_secret, access_token, access_secret):
    """Authenticate with Twitter API using OAuth 2.0"""
    try:
        client = tweepy.Client(
            bearer_token=bearer_token,
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
            wait_on_rate_limit=True
        )

        # Test the authentication by getting the user info
        me = client.get_me()
        if me and me.data:
            user_id = me.data.id
            username = me.data.username
            return client, True, f"Authentication successful! Logged in as @{username} (ID: {user_id})"
        else:
            return None, False, "Authentication successful but couldn't fetch user details"

    except Exception as e:
        error_msg = f"Authentication failed: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            error_msg += f"\nStatus: {e.response.status_code}"
            try:
                error_msg += f"\nDetails: {e.response.text}"
            except:
                pass
        return None, False, error_msg


# Function to get rate limit status
def get_rate_limit_status(client):
    """Get current rate limit status for like endpoint"""
    try:
        # Get the authenticated user
        me = client.get_me()
        if not me or not me.data:
            return {
                'remaining': "Unknown",
                'limit': "Unknown",
                'reset_time': int(time.time()) + (3 * 60)
            }

        user_id = me.data.id

        # Make a small request to get rate limit headers
        try:
            # Try to get a tweet
            response = client.get_tweet("1")
        except Exception as e:
            # Even if this fails, we might get rate limit headers
            pass

        return {
            'user_id': user_id,
            'remaining': "Unknown",
            'limit': "50 likes per 24 hours (standard)",
            'reset_time': int(time.time()) + (3 * 60)  # Estimate 15 minutes
        }
    except Exception as e:
        st.warning(f"Could not fetch rate limit status: {str(e)}")
        return {
            'remaining': "Unknown",
            'limit': "Unknown",
            'reset_time': int(time.time()) + (3 * 60)  # Default 15 minutes
        }


# Function to extract rate limit info from error
def extract_rate_limit_info(error):
    """Extract rate limit information from tweepy error"""
    reset_time = int(time.time()) + (3 * 60)
    retry_after = 3 * 60

    # Check if response headers are available
    if hasattr(error, 'response') and error.response is not None:
        headers = error.response.headers

        if 'x-rate-limit-reset' in headers:
            reset_time = int(headers['x-rate-limit-reset'])
            retry_after = max(reset_time - int(time.time()), 0)
        elif 'retry-after' in headers:
            retry_after = int(headers['retry-after'])
            reset_time = int(time.time()) + retry_after

    return {
        'reset_time': reset_time,
        'retry_after': retry_after,
        'formatted_time': time.strftime('%H:%M:%S', time.localtime(reset_time))
    }


# Function to verify tweet exists
def check_tweet(client, tweet_id):
    """Check if a tweet exists and is accessible"""
    try:
        tweet = client.get_tweet(tweet_id)
        if tweet and tweet.data:
            return True, tweet.data.text if hasattr(tweet.data, 'text') else "Tweet exists but content not accessible"
        return False, "Tweet not found or not accessible"
    except tweepy.NotFound:
        return False, "Tweet not found"
    except Exception as e:
        return False, f"Error checking tweet: {str(e)}"


# Function to like a tweet
def like_tweet(client, tweet_id):
    """Like a tweet by its ID using POST /2/users/:id/likes endpoint"""
    if not client:
        return False, "Client not authenticated. Please authenticate first."

    try:
        # First check if the tweet exists
        tweet_exists, tweet_text = check_tweet(client, tweet_id)
        if not tweet_exists:
            return False, f"Cannot like tweet: {tweet_text}"

        # Like the tweet (without passing user_id parameter)
        response = client.like(tweet_id)

        # For debugging
        st.session_state.last_response = str(response)

        # Store successful request time to track rate limits
        if 'last_request_time' not in st.session_state:
            st.session_state.last_request_time = []

        # Keep track of the last 10 requests
        st.session_state.last_request_time.append(time.time())
        if len(st.session_state.last_request_time) > 10:
            st.session_state.last_request_time.pop(0)

        return True, f"Tweet liked successfully! Response: {response}"

    except tweepy.TooManyRequests as e:
        # Extract rate limit information
        rate_info = extract_rate_limit_info(e)

        # Store rate limit info in session state
        st.session_state.rate_limited = True
        st.session_state.rate_limit_reset = rate_info['reset_time']
        st.session_state.rate_limit_retry_after = rate_info['retry_after']

        error_msg = f"Rate limit exceeded. Twitter's rate limits will reset at {rate_info['formatted_time']} (in {rate_info['retry_after']} seconds)."
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_msg += f"\nDetails: {e.response.text}"
            except:
                pass
        return False, error_msg

    except tweepy.Unauthorized as e:
        error_msg = "Authentication error. Please check your credentials."
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_msg += f"\nDetails: {e.response.text}"
            except:
                pass
        return False, error_msg

    except tweepy.NotFound as e:
        error_msg = f"Tweet with ID {tweet_id} not found."
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_msg += f"\nDetails: {e.response.text}"
            except:
                pass
        return False, error_msg

    except tweepy.Forbidden as e:
        error_msg = f"Forbidden to like tweet with ID {tweet_id}. You may lack proper permissions."
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_msg += f"\nDetails: {e.response.text}"
            except:
                pass
        return False, error_msg

    except Exception as e:
        error_msg = f"Error liking tweet: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_msg += f"\nStatus: {e.response.status_code}\nDetails: {e.response.text}"
            except:
                pass
        return False, error_msg


# Function to display app instructions
def show_instructions():
    st.markdown("""
    ## How to Use This App

    This app allows you to like tweets using the Twitter API. Follow these steps:

    1. **Setup Tab**: Enter your Twitter API credentials and authenticate
       - Bearer Token: Your Twitter API Bearer Token
       - API Key: Your Twitter API Key
       - API Secret: Your Twitter API Secret
       - Access Token: Your Twitter Access Token
       - Access Secret: Your Twitter Access Secret

    2. **Like Tweet Tab**: Enter the ID of the tweet you want to like
       - Tweet ID: The numeric ID of the tweet (e.g., 1234567890)
       - Click "Like Tweet" to like the tweet

    ### Where to Find Tweet IDs
    The Tweet ID is the number at the end of a tweet's URL:
    https://x.com/username/status/**1234567890**

    ### Understanding Twitter API Rate Limits

    Twitter API imposes strict rate limits on interactions including likes:

    - **Standard access (free)**: 50 likes per 24 hours
    - **Essential access**: 300 likes per 24 hours
    - **Elevated access**: 1,000 likes per 24 hours

    The app will:
    - Track your recent request activity
    - Show remaining estimated capacity
    - Display a countdown timer if you hit a rate limit
    - Automatically notify you when the rate limit resets

    ### Troubleshooting Common Issues

    If you're having trouble liking tweets:

    1. **Check your app permissions** in the Twitter Developer Portal
       - Ensure your app has **Read and Write** permissions
       - Verify OAuth 1.0a is enabled with proper settings

    2. **Verify tweet accessibility**
       - Make sure the tweet is public and still exists
       - You cannot like your own tweets through the API
       - You cannot like a tweet you've already liked

    3. **Examine error responses**
       - 401 errors indicate authentication problems
       - 403 errors often mean permission issues
       - 404 errors mean the tweet wasn't found
    """)


# Main function
def main():
    st.set_page_config(
        page_title="Twitter Tweet Liker",
        page_icon="🐦",
        layout="wide"
    )

    # Load secrets
    secrets = load_secrets()
    if not secrets:
        st.stop()

    # Check password
    if not check_password(secrets):
        st.stop()

    # App header
    st.title("🐦 Twitter Tweet Liker")
    st.write("A simple app to like tweets using the Twitter API")

    # Create tabs for instructions, setup, and execution
    tab1, tab2, tab3, tab4 = st.tabs(["Instructions", "Setup", "Like Tweet", "Debug"])

    with tab1:
        show_instructions()

    with tab2:
        st.header("Twitter API Credentials")
        st.write(
            "Enter your Twitter API credentials below. These will not be stored and will only be used for this session.")

        # Use default values from secrets if available
        default_bearer = secrets['twitter']['bearer_token'] if 'twitter' in secrets and 'bearer_token' in secrets[
            'twitter'] else ""
        default_api_key = secrets['twitter']['api_key'] if 'twitter' in secrets and 'api_key' in secrets[
            'twitter'] else ""
        default_api_secret = secrets['twitter']['api_secret'] if 'twitter' in secrets and 'api_secret' in secrets[
            'twitter'] else ""
        default_access_token = secrets['twitter']['access_token'] if 'twitter' in secrets and 'access_token' in secrets[
            'twitter'] else ""
        default_access_secret = secrets['twitter']['access_secret'] if 'twitter' in secrets and 'access_secret' in \
                                                                       secrets['twitter'] else ""

        # API credentials input
        bearer_token = st.text_input("Bearer Token", value=default_bearer, type="password")
        api_key = st.text_input("API Key", value=default_api_key, type="password")
        api_secret = st.text_input("API Secret", value=default_api_secret, type="password")
        access_token = st.text_input("Access Token", value=default_access_token, type="password")
        access_secret = st.text_input("Access Secret", value=default_access_secret, type="password")

        # Initialize session state for API client
        if 'client' not in st.session_state:
            st.session_state.client = None
            st.session_state.authenticated = False
            st.session_state.user_info = None

        # Authentication button
        if st.button("Authenticate"):
            if not all([bearer_token, api_key, api_secret, access_token, access_secret]):
                st.error("Please fill in all credentials")
            else:
                client, success, message = authenticate_twitter(
                    bearer_token, api_key, api_secret, access_token, access_secret
                )
                if success:
                    st.success(message)
                    st.session_state.client = client
                    st.session_state.authenticated = True

                    # Store user info for reference
                    try:
                        me = client.get_me()
                        if me and me.data:
                            st.session_state.user_info = {
                                "id": me.data.id,
                                "username": me.data.username
                            }
                    except Exception as e:
                        st.warning(f"Authenticated but couldn't get user details: {str(e)}")
                else:
                    st.error(message)
                    st.session_state.authenticated = False
                    st.session_state.client = None
                    st.session_state.user_info = None

    with tab3:
        st.header("Like a Tweet")

        if not st.session_state.get('authenticated', False):
            st.warning("Please authenticate in the Setup tab first")
        else:
            # Display user info
            if st.session_state.get('user_info'):
                st.info(
                    f"Authenticated as @{st.session_state.user_info['username']} (ID: {st.session_state.user_info['id']})")

            # Display rate limit information if available
            col1, col2 = st.columns(2)

            with col1:
                st.write("Enter the ID of the tweet you want to like:")
                tweet_id = st.text_input("Tweet ID")

                # Add a tweet preview option
                if tweet_id:
                    if st.button("Preview Tweet"):
                        with st.spinner("Fetching tweet..."):
                            exists, text = check_tweet(st.session_state.client, tweet_id)
                            if exists:
                                st.success("Tweet found!")
                                st.markdown(
                                    f"**Tweet text preview:**\n> {text[:100]}{'...' if len(text) > 100 else ''}")
                            else:
                                st.error(f"Tweet issue: {text}")

            with col2:
                st.subheader("Rate Limit Status")

                # Calculate estimated rate limit from session state
                if 'last_request_time' in st.session_state and st.session_state.last_request_time:
                    recent_count = len(st.session_state.last_request_time)
                    oldest_time = st.session_state.last_request_time[0]
                    time_span = time.time() - oldest_time
                    st.write(f"Recent requests: {recent_count} in the last {time_span:.0f} seconds")

                    # Rough estimate of remaining capacity
                    est_remaining = max(50 - (recent_count * (24 * 60 * 60) / max(time_span, 1)), 0)
                    st.write(f"Estimated remaining capacity: ~{est_remaining:.0f} requests")
                else:
                    st.write("No recent requests tracked")

                # Show rate limit warning if rate limited
                if st.session_state.get('rate_limited', False):
                    remaining_time = max(st.session_state.rate_limit_reset - time.time(), 0)
                    if remaining_time <= 0:
                        # Reset rate limit status
                        st.session_state.rate_limited = False
                    else:
                        st.warning(f"⚠️ Rate limited! Resets in {remaining_time:.0f} seconds")
                        formatted_time = time.strftime('%H:%M:%S',
                                                       time.localtime(st.session_state.rate_limit_reset))
                        st.write(f"Reset time: {formatted_time}")

            # Rate limit info
            with st.expander("About Twitter Rate Limits"):
                st.markdown("""
                ### Twitter API Rate Limits

                The Twitter API v2 has the following rate limits for likes:

                - **Standard access (free)**: 50 likes per 24 hours
                - **Essential access**: 300 likes per 24 hours
                - **Elevated access**: 1,000 likes per 24 hours

                If you hit a rate limit:
                1. The app will show you when the limit resets
                2. You'll need to wait until that time before making more requests
                3. Continually hitting rate limits may affect your API access

                
                """)

            # Like tweet button
            if st.button("Like Tweet"):
                if not tweet_id:
                    st.error("Please enter a Tweet ID")
                # Check if currently rate limited
                elif st.session_state.get('rate_limited', False):
                    remaining_time = max(st.session_state.rate_limit_reset - time.time(), 0)
                    if remaining_time > 0:
                        st.error(
                            f"Currently rate limited. Please wait {remaining_time:.0f} seconds before trying again.")
                    else:
                        # Reset rate limit status and try again
                        st.session_state.rate_limited = False
                        with st.spinner("Liking tweet..."):
                            success, message = like_tweet(st.session_state.client, tweet_id)
                            if success:
                                st.success(message)
                            else:
                                st.error(message)
                else:
                    with st.spinner("Liking tweet..."):
                        success, message = like_tweet(st.session_state.client, tweet_id)
                        if success:
                            st.success(message)
                        else:
                            st.error(message)

                            # If rate limited, show retry button with countdown
                            if st.session_state.get('rate_limited', False):
                                reset_time = st.session_state.rate_limit_reset

                                # Display a countdown timer
                                ph = st.empty()
                                for remaining in range(int(st.session_state.rate_limit_retry_after), 0, -1):
                                    if st.session_state.get('cancel_countdown', False):
                                        break

                                    # Update every second
                                    minutes, seconds = divmod(remaining, 60)
                                    countdown_text = f"{minutes:02d}:{seconds:02d}"
                                    ph.metric("Time until rate limit reset", countdown_text)
                                    time.sleep(1)

                                # Reset countdown cancellation flag
                                st.session_state.cancel_countdown = False

                                # Clear placeholder after countdown
                                ph.empty()

                                # Reset rate limited status after countdown
                                st.session_state.rate_limited = False
                                st.success("Rate limit reset. You can try again now.")

    # Debug tab
    with tab4:
        st.header("Debug Information")
        st.write("This tab shows technical information to help troubleshoot issues.")

        # Show authentication status
        st.subheader("Authentication Status")
        auth_status = "Authenticated ✅" if st.session_state.get('authenticated', False) else "Not Authenticated ❌"
        st.write(f"Status: {auth_status}")

        if st.session_state.get('user_info'):
            st.json(st.session_state.user_info)

        # Show last API response if available
        st.subheader("Last API Response")
        if 'last_response' in st.session_state:
            st.code(st.session_state.last_response)
        else:
            st.write("No API responses recorded yet")

        # Show session state (excluding client object)
        st.subheader("Session State")
        session_state_copy = {k: v for k, v in st.session_state.items() if k != 'client'}
        st.json(session_state_copy)

        # Show Tweepy version
        st.subheader("Library Versions")
        st.write(f"Tweepy Version: {tweepy.__version__}")

        # Manual tweet check
        st.subheader("Check Tweet")
        manual_tweet_id = st.text_input("Enter Tweet ID to check", key="debug_tweet_id")
        if st.button("Check Tweet") and manual_tweet_id and st.session_state.get('client'):
            try:
                tweet = st.session_state.client.get_tweet(manual_tweet_id)
                if tweet and tweet.data:
                    st.success("Tweet exists")
                    st.json(tweet.data)
                else:
                    st.error("Tweet not found or no data returned")
            except Exception as e:
                st.error(f"Error checking tweet: {str(e)}")
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        st.error(f"Response status: {e.response.status_code}")
                        st.code(e.response.text)
                    except:
                        pass


if __name__ == "__main__":
    main()