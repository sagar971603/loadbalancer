import argparse
import json
import logging

from registration_orchestrator import RegistrationOptions, register_individual


class ConsoleOtpProvider:
    def get_aadhaar_otp(self, context):
        print("Aadhaar OTP context:", json.dumps(context, indent=2))
        return input("Enter Aadhaar OTP: ").strip()

    def get_contact_otps(self, context):
        print("Contact OTP context:", json.dumps(context, indent=2))
        mobile_otp = input("Enter mobile OTP: ").strip()
        email_otp = input("Enter email OTP: ").strip()
        return mobile_otp, email_otp


def main():
    parser = argparse.ArgumentParser(description="Run ePortal individual registration")
    parser.add_argument("--user-json", required=True, help="Path to user details JSON file")
    parser.add_argument("--submit-final", action="store_true", help="Actually submit Step 5")
    parser.add_argument("--verify-login", action="store_true", help="Verify login after final success")
    parser.add_argument("--browser-type", default="firefox", choices=["firefox", "chromium", "webkit"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with open(args.user_json, "r", encoding="utf-8") as f:
        user_details = json.load(f)

    result = register_individual(
        user_details,
        ConsoleOtpProvider(),
        RegistrationOptions(
            submit_final=args.submit_final,
            browser_type=args.browser_type,
            verify_login=args.verify_login,
        ),
        on_step=lambda name, res: print(name, json.dumps(res, indent=2)),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
