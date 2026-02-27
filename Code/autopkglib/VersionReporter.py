#!/usr/local/autopkg/python
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""See docstring for VersionReporter class"""

import json

from autopkglib.URLGetter import URLGetter

__all__ = ["VersionReporter"]


class VersionReporter(URLGetter):
    """Reports app version information to an Azure Function API after a
    successful AutoPkg run.  Intended for use as a postprocessor with
    ``autopkg run --post``.

    The API endpoint is used by the macOS Update Agent to determine
    which apps have available updates.
    """

    description = __doc__
    input_variables = {
        "VERSION_REPORT_URL": {
            "required": True,
            "description": (
                "Full URL to the Azure Function register-version endpoint, "
                "e.g. https://myfunc.azurewebsites.net/api/register-version"
            ),
        },
        "VERSION_REPORT_API_KEY": {
            "required": True,
            "description": "Azure Function API key (x-functions-key header).",
        },
        "bundleid": {
            "required": True,
            "description": "The CFBundleIdentifier of the app.",
        },
        "version": {
            "required": True,
            "description": "The latest version string of the app.",
        },
        "NAME": {
            "required": False,
            "description": "Display name of the app.",
        },
        "VERSION_REPORT_BLOB_PATH": {
            "required": False,
            "description": (
                "Blob Storage path for the package file, "
                "e.g. org.mozilla.firefox/Firefox-115.0.1.pkg"
            ),
        },
        "VERSION_REPORT_INTUNE_APP_ID": {
            "required": False,
            "description": "Intune app ID if available.",
        },
    }
    output_variables = {
        "version_report_status": {
            "description": "HTTP status code from the API call.",
        },
        "version_reported": {
            "description": "True if the version was successfully reported.",
        },
    }

    def main(self):
        url = self.env["VERSION_REPORT_URL"]
        api_key = self.env["VERSION_REPORT_API_KEY"]
        bundle_id = self.env["bundleid"]
        version = self.env["version"]
        app_name = self.env.get("NAME", "")

        payload = {
            "bundle_id": bundle_id,
            "app_name": app_name,
            "latest_version": version,
            "blob_path": self.env.get("VERSION_REPORT_BLOB_PATH", ""),
            "intune_app_id": self.env.get("VERSION_REPORT_INTUNE_APP_ID", ""),
            "recipe_id": self.env.get("RECIPE_PATH", ""),
        }

        self.output(f"Reporting {bundle_id} v{version} to {url}")

        try:
            curl_cmd = self.prepare_curl_cmd()
            curl_cmd.extend(["--url", url])
            curl_cmd.extend(["-X", "POST"])
            curl_cmd.extend(["-H", "Content-Type: application/json"])
            curl_cmd.extend(["-H", f"x-functions-key: {api_key}"])
            curl_cmd.extend(["-d", json.dumps(payload)])

            self.download_with_curl(curl_cmd)
            self.env["version_reported"] = True
            self.env["version_report_status"] = "200"
            self.output(f"Successfully reported {bundle_id} v{version}")

        except Exception as err:
            self.output(f"WARNING: Failed to report version: {err}")
            self.env["version_reported"] = False
            self.env["version_report_status"] = "error"


if __name__ == "__main__":
    PROCESSOR = VersionReporter()
    PROCESSOR.execute_shell()
