#!/usr/bin/env python3
"""
Avahi Configuration Module

This module provides functionality to configure the Avahi daemon
to only advertise on physical network interfaces (eth*, wlan*).
"""

import os
import sys
import argparse
import logging
import shutil
import subprocess


AVAHI_CONF = "/etc/avahi/avahi-daemon.conf"
ALLOW_INTERFACES_LINE = 'allow-interfaces=eth0,wlan0\n'


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s',
        stream=sys.stderr
    )


def _filter_server_interface_rules(lines: list[str]) -> tuple[list[str], bool, bool, bool]:
    """Remove existing interface rules from the [server] section."""
    modified = False
    new_lines: list[str] = []
    in_server_section = False
    found_allow_interfaces = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('[server]'):
            in_server_section = True
            new_lines.append(line)
            continue

        if stripped.startswith('[') and stripped.endswith(']'):
            in_server_section = False
            new_lines.append(line)
            continue

        if in_server_section and (stripped.startswith('allow-interfaces=') or
                                  stripped.startswith('#allow-interfaces=')):
            found_allow_interfaces = True
            modified = True
            continue

        if in_server_section and (stripped.startswith('deny-interfaces=') or
                                  stripped.startswith('#deny-interfaces=')):
            modified = True
            continue

        new_lines.append(line)

    return new_lines, modified, found_allow_interfaces, in_server_section


def _ensure_allow_interfaces(lines: list[str]) -> tuple[list[str], bool]:
    """Ensure allow-interfaces is present in [server] section."""
    final_lines: list[str] = []
    in_server_section = False
    server_section_processed = False
    modified = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('[server]'):
            in_server_section = True
            server_section_processed = False
            final_lines.append(line)
            continue

        if stripped.startswith('[') and stripped.endswith(']'):
            if in_server_section and not server_section_processed:
                final_lines.append(ALLOW_INTERFACES_LINE)
                server_section_processed = True
                modified = True
            in_server_section = False
            final_lines.append(line)
            continue

        final_lines.append(line)

    if in_server_section and not server_section_processed:
        final_lines.append(ALLOW_INTERFACES_LINE)
        modified = True

    return final_lines, modified


def _restart_or_start_avahi() -> bool:
    """Apply new Avahi config by restarting if active, otherwise trying to start."""
    try:
        check_result = subprocess.run(
            ['systemctl', 'is-active', 'avahi-daemon'],
            capture_output=True,
            text=True,
            check=False
        )

        if check_result.returncode == 0:
            result = subprocess.run(
                ['systemctl', 'restart', 'avahi-daemon'],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0:
                logging.info("Restarted Avahi daemon successfully")
                return True

            logging.warning("Failed to restart Avahi daemon: %s", result.stderr.strip())
            return False

        logging.info("Avahi daemon is not running, attempting to start it")
        result = subprocess.run(
            ['systemctl', 'start', 'avahi-daemon'],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            logging.info("Started Avahi daemon successfully")
            return True

        logging.warning("Failed to start Avahi daemon: %s", result.stderr.strip())
        logging.info("Configuration updated but daemon could not be started")
        return True

    except (subprocess.SubprocessError, OSError) as e:
        logging.warning("Error restarting Avahi daemon: %s", e)
        logging.info("Configuration updated but daemon restart failed")
        return True


def configure_avahi_interfaces() -> bool:
    """
    Configure Avahi daemon to only advertise on physical interfaces (eth*, wlan*)

    This function modifies /etc/avahi/avahi-daemon.conf to:
    - Allow only physical ethernet and wireless interfaces

    Returns:
        True if successful, False otherwise
    """
    try:
        # Check if Avahi is installed
        if not os.path.exists(AVAHI_CONF):
            logging.info("Avahi daemon not installed, skipping configuration")
            return True

        # Read current configuration
        with open(AVAHI_CONF, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        (
            new_lines,
            modified,
            found_allow_interfaces,
            in_server_section,
        ) = _filter_server_interface_rules(lines)

        # Add our interface configuration to the [server] section
        if in_server_section or not found_allow_interfaces:
            new_lines, added = _ensure_allow_interfaces(new_lines)
            modified = modified or added

        if not modified:
            logging.info("Avahi configuration already correct")
            return True

        # Write the modified configuration if changes were made
        backup_file = f"{AVAHI_CONF}.backup"
        if not os.path.exists(backup_file):
            shutil.copy2(AVAHI_CONF, backup_file)
            logging.info("Created backup: %s", backup_file)

        with open(AVAHI_CONF, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        logging.info("Updated Avahi configuration to only advertise on physical interfaces")
        logging.info("Restarting Avahi daemon to apply configuration changes")
        if not _restart_or_start_avahi():
            return False

        return True

    except OSError as e:
        logging.error("Error configuring Avahi daemon: %s", e)
        return False


def check_root_privileges():
    """
    Check if the script is running as root

    Returns:
        True if running as root, False otherwise
    """
    if os.geteuid() != 0:
        logging.error("This script must be run as root (use sudo)")
        return False
    return True


def _check_only_result() -> int:
    """Check current Avahi configuration without applying changes."""
    if not os.path.exists(AVAHI_CONF):
        print("Avahi daemon not installed")
        return 0

    try:
        with open(AVAHI_CONF, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'allow-interfaces=eth0,wlan0' in content:
            print("Avahi configuration is correct - only advertising on physical interfaces")
            return 0

        print("Avahi configuration needs updating")
        return 1
    except OSError as e:
        logging.error("Error reading Avahi configuration: %s", e)
        return 1


def main() -> int:
    """Main function for config-avahi command"""
    parser = argparse.ArgumentParser(
        description='Configure Avahi daemon to only advertise on physical interfaces'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Only check current configuration, do not modify'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Check if running as root (required for modifications)
    if not args.check_only and not check_root_privileges():
        return 1

    if args.check_only:
        return _check_only_result()

    # Configure Avahi
    if configure_avahi_interfaces():
        logging.info("Avahi configuration completed successfully")
        return 0

    logging.error("Avahi configuration failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
