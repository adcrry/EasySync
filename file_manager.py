import hashlib
import os
from difflib import unified_diff
import shutil
import subprocess
from dataclasses import dataclass


ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_CYAN = "\033[36m"
ANSI_YELLOW = "\033[33m"


def _sha256_file(file_path, chunk_size=1024 * 1024):
	digest = hashlib.sha256()
	with open(file_path, "rb") as file_handle:
		while True:
			chunk = file_handle.read(chunk_size)
			if not chunk:
				break
			digest.update(chunk)
	return digest.hexdigest()


def files_are_different(source_file, destination_file):
	if destination_file.stat().st_size != source_file.stat().st_size:
		return True
	return _sha256_file(source_file) != _sha256_file(destination_file)


def _read_text_lines(file_path):
	"""Return text lines or None if file is not decodable as UTF-8 text."""
	try:
		with open(file_path, "r", encoding="utf-8") as file_handle:
			return file_handle.readlines()
	except (UnicodeDecodeError, OSError):
		return None


def get_conflict_diff(source_file, destination_file):
	"""Build a unified diff for a conflicting source/destination file pair."""
	if not destination_file.exists() or not destination_file.is_file():
		return None

	if not files_are_different(source_file, destination_file):
		return None

	source_lines = _read_text_lines(source_file)
	destination_lines = _read_text_lines(destination_file)

	if source_lines is None or destination_lines is None:
		return "Binary or non UTF-8 text file; diff cannot be shown."

	diff_lines = list(
		unified_diff(
			destination_lines,
			source_lines,
			fromfile=str(destination_file),
			tofile=str(source_file),
			lineterm="",
		)
	)

	if not diff_lines:
		return None

	return "\n".join(diff_lines)


def format_diff_git_style(diff_text):
	"""Apply git-like terminal colors to unified diff text."""
	if not diff_text:
		return diff_text

	formatted_lines = []
	for line in diff_text.splitlines():
		if line.startswith("+++") or line.startswith("---"):
			formatted_lines.append(f"{ANSI_CYAN}{line}{ANSI_RESET}")
		elif line.startswith("@@"):
			formatted_lines.append(f"{ANSI_CYAN}{line}{ANSI_RESET}")
		elif line.startswith("+"):
			formatted_lines.append(f"{ANSI_GREEN}{line}{ANSI_RESET}")
		elif line.startswith("-"):
			formatted_lines.append(f"{ANSI_RED}{line}{ANSI_RESET}")
		else:
			formatted_lines.append(line)

	return "\n".join(formatted_lines)


@dataclass
class BackupAction:
	source_file: object
	destination_root: object
	destination_file: object
	relative_path: object
	action_type: str


@dataclass
class PlanSummary:
	new_files: int = 0
	overwritten_files: int = 0
	unchanged_files: int = 0

	@property
	def actionable_files(self):
		return self.new_files + self.overwritten_files


@dataclass
class BackupResult:
	copied_files: int = 0
	overwritten_files: int = 0


@dataclass
class RestoreAction:
	selected_destination_file: object
	selected_destination_root: object
	target_source_file: object
	relative_path: object
	action_type: str
	has_destination_conflict: bool = False
	candidate_files: tuple = ()


@dataclass
class RestoreSummary:
	new_files: int = 0
	overwritten_files: int = 0
	unchanged_files: int = 0
	destination_conflicts: int = 0

	@property
	def actionable_files(self):
		return self.new_files + self.overwritten_files


@dataclass
class RestoreResult:
	copied_files: int = 0
	overwritten_files: int = 0


def _copy_file_with_system_tool(source_file, destination_file):
	"""Copy using robocopy on Windows or rsync on other platforms."""
	if os.name == "nt":
		try:
			completed_process = subprocess.run(
				[
					"robocopy",
					str(source_file.parent),
					str(destination_file.parent),
					str(source_file.name),
					"/R:1",
					"/W:1",
					"/NFL",
					"/NDL",
					"/NJH",
					"/NJS",
					"/NC",
					"/NS",
					"/NP",
				],
				capture_output=True,
				text=True,
			)
		except FileNotFoundError:
			return False

		if completed_process.returncode > 7:
			raise RuntimeError(
				f"robocopy failed for '{source_file}' -> '{destination_file}': "
				f"{completed_process.stderr or completed_process.stdout}"
			)

		return True
	
	try:
		completed_process = subprocess.run(
			["rsync", "-a", "--", str(source_file), str(destination_file)],
			capture_output=True,
			text=True,
		)
	except FileNotFoundError:
		return False

	if completed_process.returncode != 0:
		raise RuntimeError(
			f"rsync failed for '{source_file}' -> '{destination_file}': "
			f"{completed_process.stderr or completed_process.stdout}"
		)

	return True


def build_backup_plan(profile, destinations=None):
	source_root = profile.source
	if not source_root.exists() or not source_root.is_dir():
		raise ValueError(f"Source folder does not exist or is not a directory: {source_root}")

	selected_destinations = profile.destinations if destinations is None else destinations

	source_files = [path for path in source_root.rglob("*") if path.is_file()]
	plan = []

	for source_file in source_files:
		relative_path = source_file.relative_to(source_root)
		for destination_root in selected_destinations:
			destination_file = destination_root / relative_path

			if not destination_file.exists():
				action_type = "new"
			elif files_are_different(source_file, destination_file):
				action_type = "overwrite"
			else:
				action_type = "unchanged"

			plan.append(
				BackupAction(
					source_file=source_file,
					destination_root=destination_root,
					destination_file=destination_file,
					relative_path=relative_path,
					action_type=action_type,
				)
			)

	return plan


def _pick_restore_candidate(candidate_files):
	"""Pick the best file among destinations and mark cross-destination conflicts."""
	hashes = {_sha256_file(candidate_file) for candidate_file in candidate_files}
	has_conflict = len(hashes) > 1
	selected = max(
		candidate_files,
		key=lambda path: (path.stat().st_mtime, str(path)),
	)
	return selected, has_conflict


def build_restore_plan(profile, destinations=None):
	source_root = profile.source
	selected_destinations = profile.destinations if destinations is None else destinations

	restore_candidates = {}
	for destination_root in selected_destinations:
		if not destination_root.exists() or not destination_root.is_dir():
			continue
		for destination_file in destination_root.rglob("*"):
			if not destination_file.is_file():
				continue
			relative_path = destination_file.relative_to(destination_root)
			restore_candidates.setdefault(relative_path, []).append(
				(destination_root, destination_file)
			)

	plan = []
	for relative_path, candidates in restore_candidates.items():
		candidate_files = [candidate_file for _, candidate_file in candidates]
		selected_file, has_destination_conflict = _pick_restore_candidate(candidate_files)
		selected_root = next(
			destination_root
			for destination_root, candidate_file in candidates
			if candidate_file == selected_file
		)

		target_source_file = source_root / relative_path
		if not target_source_file.exists():
			action_type = "new"
		elif files_are_different(selected_file, target_source_file):
			action_type = "overwrite"
		else:
			action_type = "unchanged"

		plan.append(
			RestoreAction(
				selected_destination_file=selected_file,
				selected_destination_root=selected_root,
				target_source_file=target_source_file,
				relative_path=relative_path,
				action_type=action_type,
				has_destination_conflict=has_destination_conflict,
				candidate_files=tuple(candidate_files),
			)
		)

	return plan


def summarize_plan(plan):
	summary = PlanSummary()

	for action in plan:
		if action.action_type == "new":
			summary.new_files += 1
		elif action.action_type == "overwrite":
			summary.overwritten_files += 1
		else:
			summary.unchanged_files += 1

	return summary


def summarize_restore_plan(plan):
	summary = RestoreSummary()

	for action in plan:
		if action.action_type == "new":
			summary.new_files += 1
		elif action.action_type == "overwrite":
			summary.overwritten_files += 1
		else:
			summary.unchanged_files += 1

		if action.has_destination_conflict:
			summary.destination_conflicts += 1

	return summary


def execute_backup_plan(plan):
	result = BackupResult()
	warned_missing_roots = set()

	for action in plan:
		if action.action_type == "unchanged":
			continue

		if not action.destination_root.exists() or not action.destination_root.is_dir():
			if action.destination_root not in warned_missing_roots:
				print(
					f"{ANSI_YELLOW}Warning: target folder does not exist, skipping: "
					f"{action.destination_root}{ANSI_RESET}"
				)
				warned_missing_roots.add(action.destination_root)
			continue

		action.destination_file.parent.mkdir(parents=True, exist_ok=True)
		try:
			copied_with_system_tool = _copy_file_with_system_tool(
				action.source_file,
				action.destination_file,
			)
			if not copied_with_system_tool:
				shutil.copy2(action.source_file, action.destination_file)
		except (OSError, RuntimeError) as error:
			print(
				f"{ANSI_YELLOW}Warning: failed to copy '{action.source_file}' to "
				f"'{action.destination_file}': {error}{ANSI_RESET}"
			)
			continue

		result.copied_files += 1
		if action.action_type == "overwrite":
			result.overwritten_files += 1

	return result


def execute_restore_plan(plan):
	result = RestoreResult()
	warned_missing_roots = set()

	for action in plan:
		if action.action_type == "unchanged":
			continue

		if (
			not action.selected_destination_root.exists()
			or not action.selected_destination_root.is_dir()
		):
			if action.selected_destination_root not in warned_missing_roots:
				print(
					f"{ANSI_YELLOW}Warning: source destination folder does not exist, "
					f"skipping: {action.selected_destination_root}{ANSI_RESET}"
				)
				warned_missing_roots.add(action.selected_destination_root)
			continue

		if not action.selected_destination_file.exists() or not action.selected_destination_file.is_file():
			print(
				f"{ANSI_YELLOW}Warning: source file not found for restore, skipping: "
				f"{action.selected_destination_file}{ANSI_RESET}"
			)
			continue

		action.target_source_file.parent.mkdir(parents=True, exist_ok=True)
		try:
			copied_with_system_tool = _copy_file_with_system_tool(
				action.selected_destination_file,
				action.target_source_file,
			)
			if not copied_with_system_tool:
				shutil.copy2(action.selected_destination_file, action.target_source_file)
		except (OSError, RuntimeError) as error:
			print(
				f"{ANSI_YELLOW}Warning: failed to restore '{action.selected_destination_file}' "
				f"to '{action.target_source_file}': {error}{ANSI_RESET}"
			)
			continue

		result.copied_files += 1
		if action.action_type == "overwrite":
			result.overwritten_files += 1

	return result
