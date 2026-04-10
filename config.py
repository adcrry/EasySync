
import json
import os
import platform
import uuid
from pathlib import Path


def _dedupe_keep_order(values):
    seen = set()
    deduped = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def get_current_computer_identifiers():
    env_ids = [
        os.environ.get("EASYBACKUP_COMPUTER_ID"),
        os.environ.get("COMPUTERNAME"),
        os.environ.get("HOSTNAME"),
    ]
    hostname = platform.node()
    machine_id = f"{uuid.getnode():012x}"
    return _dedupe_keep_order([*env_ids, hostname, machine_id])


class ComputerProfile:

    def __init__(self, computer_id, source, destinations):
        self.computer_id = computer_id
        self.source = Path(source)
        self.destinations = [Path(destination) for destination in destinations]

    @staticmethod
    def from_json(profile_name, computer_id, json_data):
        if not isinstance(json_data, dict):
            raise ValueError(
                f"Profile '{profile_name}' computer section '{computer_id}' must be an object"
            )

        source = json_data.get("source")
        destinations = json_data.get("destinations")

        if not source:
            raise ValueError(
                f"Profile '{profile_name}' computer section '{computer_id}' is missing "
                "required field: source"
            )
        if not isinstance(destinations, list) or not destinations:
            raise ValueError(
                f"Profile '{profile_name}' computer section '{computer_id}' must define "
                "a non-empty destinations list"
            )

        return ComputerProfile(computer_id, source, destinations)


class Profile:

    def __init__(self, name, computers):
        self.name = name
        self.computers = computers

    @staticmethod
    def from_json(json_data):
        name = json_data.get("name")
        computers_data = json_data.get("computers")

        if not name:
            raise ValueError("Profile is missing required field: name")
        if not isinstance(computers_data, dict) or not computers_data:
            raise ValueError(
                f"Profile '{name}' must define a non-empty 'computers' object"
            )

        computers = {}
        for computer_id, computer_json in computers_data.items():
            if not computer_id:
                raise ValueError(
                    f"Profile '{name}' has a computer section with an empty id"
                )
            computers[computer_id] = ComputerProfile.from_json(
                name,
                computer_id,
                computer_json,
            )

        return Profile(name, computers)

    def get_current_computer_profile(self):
        identifiers = get_current_computer_identifiers()
        casefolded_ids = {
            configured_id.casefold(): configured_id for configured_id in self.computers
        }

        for identifier in identifiers:
            matched_id = casefolded_ids.get(identifier.casefold())
            if matched_id:
                return self.computers[matched_id]

        available = ", ".join(sorted(self.computers.keys()))
        detected = ", ".join(identifiers)
        raise ValueError(
            f"Profile '{self.name}' has no computer section for this machine. "
            f"Detected ids: {detected}. Configured ids: {available}."
        )


class BackupConfig:

    def __init__(self, profiles):
        self.profiles = profiles

    @staticmethod
    def from_json(json_data):
        profiles = [
            Profile.from_json(profile_data)
            for profile_data in json_data.get("profiles", [])
        ]
        return BackupConfig(profiles)

    @staticmethod
    def from_file(config_path):
        with open(config_path, "r", encoding="utf-8") as config_file:
            json_data = json.load(config_file)
        return BackupConfig.from_json(json_data)

    def get_profile(self, profile_name):
        for profile in self.profiles:
            if profile.name == profile_name:
                return profile
        return None