"""This module contains the class Rules. The rules are stored in the disk as a JSON file, and are loaded into the Rules object before the extraction process begins. The rules are used to determine which columns and row are to be extracted from a given CbC report and which names should be used."""
import json
import re

from .cbc_report import CbCReport
from .utils import partition
from .exceptions import RulesError

__all__ = ["Rules"]
class Rules:
    """Class representing all the rules for the extraction of (usually) multiple CbC reports. Rules exist along 2 axes: rules for column names vs for jurisdiction codes; regex rules vs strict rules. When prompted due to unknown name, the operator sets the scope of a rule being created: it may apply to all reports, to all reports of a given MNC, or to a given report. This handles queries to the rules (of the form "given this CbCR report and the source, what is the applicable sink?"), and can write the rules to a file."""
    def __init__(self, rules : str):
        try:
            try:
                self._all = json.loads(rules)
            except json.decoder.JSONDecodeError as exc:
                with open(rules, mode="r", encoding="utf-8") as infile:
                    self._all = json.load(infile)
            self._column = self._all["column_rules"]
            self._jurisdiction = self._all["jurisdiction_rules"]
        except FileNotFoundError as exc:
            raise RulesError("Rules file not found") from exc
        except KeyError as exc:
            raise RulesError("Rules file malformed") from exc
    @property
    def column(self):
        return self._column

    def write(self, rules_file):
        with open(rules_file, "w", encoding="utf-8") as json_file:
            json.dump(self._all, json_file, indent=4)

    def get_sink_from_strict(self, report: CbCReport, source: str, col_or_jur):
        """col is 'c', jur is 'j'.
        if sink not found, return None"""
        try:
            return self.__get_rules(col_or_jur, "s", report).get(source)
        except KeyError:
            return None

    def get_sink_from_regex(self, report: CbCReport, source: str, col_or_jur):
        """if not applicable, return None"""
        for source_rule, sink in self.__get_rules(col_or_jur, "r", report).items():
            source_rule_compiled = re.compile(source_rule)
            if re.match(source_rule_compiled, source):
                return sink
        return None

    def __get_rules(self, col_or_jur, strict_or_regex, report: CbCReport):
        """
        col_or_jur = `c` for columns, `j` for jurisdictions
        strict_or_regex = `s` for strict, `r` for regex
        gets sink from objects with justifications
        returns {regex_in_effect : X,
                strict_in_effect : Z}
        """
        rule_book = self._column if col_or_jur == "c" else self._jurisdiction
        mnc = report.group_name
        year = report.end_of_year

        default_all_files_rules = rule_book["default"]
        try:
            year_rules = rule_book[mnc][year]
        except KeyError:
            year_rules = dict()
        try:
            mne_rules = rule_book[mnc]["default"]
        except KeyError:
            mne_rules = dict()
        try:
            # this prioritizes year, then mne then default.
            rules_in_effect = {**default_all_files_rules, **mne_rules, **year_rules}
            for k, v in rules_in_effect.items():
                if isinstance(v, dict):
                    rules_in_effect[k] = v["sink"]
            strict, regex = partition(
                lambda x: re.search(r"_regex_(.*)", x[0]), rules_in_effect.items()
            )
            regex_dict = dict(
                (re.search(r"_regex_(.*)", x[0]).group(1), x[1]) for x in regex
            )
            return dict(strict) if strict_or_regex == "s" else regex_dict

        except Exception as excep:
            raise ValueError("Couldn't unify MNC rules. Fix 'rules.json'.") from excep

    def get_std_colnames_from_rules(self):
        # design decision: just return the IRS std columns?
        def iterate_multidimensional(my_dict: dict):
            out = []
            for k, v in my_dict.items():
                if isinstance(v, dict):
                    out += iterate_multidimensional(v)
                    continue
                elif k not in ["justification", "comment", "note"]:
                    out.append(v)
            return out

        IRS_columns = [
            "unrelated_revenues",
            "related_revenues",
            "total_revenues",
            "profit_before_tax",
            "tax_paid",
            "tax_accrued",
            "stated_capital",
            "accumulated_earnings",
            "employees",
            "tangible_assets",
        ]
        temp = set(IRS_columns + iterate_multidimensional(self._column))
        temp.discard("to_drop")
        out = list(temp)
        out.sort()
        return out

    def write_new_rule(
        self, source, mode, sink, justification, col_or_jur: str, report: CbCReport
    ):
        """Note that column names would not be shown to operator if any rule applied. thus no overwriting possible."""
        rule_set = self._column if col_or_jur == "c" else self._jurisdiction
        company = report.group_name
        year = report.end_of_year
        pair = {"sink": sink, "justification": justification}
        if mode == "!":
            # overwriting.. should warn first
            rule_set["default"][source] = pair
        elif mode == "#":
            try:
                rule_set[company]["default"][source] = pair
            except KeyError:
                try:
                    rule_set[company]["default"] = {source: pair}
                except KeyError:
                    rule_set[company] = {"default": {source: pair}}
        elif mode == ".":
            try:
                rule_set[company][year][source] = pair
            except KeyError:
                try:
                    rule_set[company][year] = {source: pair}
                except KeyError:
                    rule_set[company] = {year: {source: pair}}

    def export_justifications_to_csv(self, path : str) -> None:
        """Exports all justifications to a csv file. These are collected from the rules.json file - which is automatically updated when the operator is prompted due to unknown column or jurisdiction names."""
        def extract_objects(json_obj, keys=None, level=0):
            out = []
            if not keys:
                keys = []
            if isinstance(json_obj, dict):
                for key, value in json_obj.items():
                    if level == 3 and isinstance(value, (dict, list)):
                        out.append(
                            f"{','.join([k for k in keys+[key]])},{value['sink']},\"{value['justification']}\""
                        )

                    elif isinstance(value, (dict, list)):
                        out += extract_objects(value, keys + [key], level + 1)
            return out

        header = "type_of_rule, mnc, report_end_of_year, column_name_found, column_name_assigned, justification\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(extract_objects(self._all)))
