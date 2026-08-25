"""Ind AS standard-number -> subject map (extracted from the PDF title pages).

Annual reports describe accounting by topic (e.g. "Inventories"), not by
standard number ("Ind AS 2"). This map lets the annual-report retriever
expand a query like "Ind AS 2" into its subject keywords so the relevant
company disclosures actually surface.
"""

from __future__ import annotations

IND_AS_SUBJECTS: dict[int, str] = {
    1: "Presentation of Financial Statements",
    2: "Inventories",
    7: "Statement of Cash Flows",
    8: "Accounting Policies, Changes in Accounting Estimates and Errors",
    10: "Events after the Reporting Period",
    12: "Income Taxes",
    16: "Property, Plant and Equipment",
    19: "Employee Benefits",
    20: "Government Grants and Government Assistance",
    21: "The Effects of Changes in Foreign Exchange Rates",
    23: "Borrowing Costs",
    24: "Related Party Disclosures",
    27: "Separate Financial Statements",
    28: "Investments in Associates and Joint Ventures",
    29: "Financial Reporting in Hyperinflationary Economies",
    32: "Financial Instruments: Presentation",
    33: "Earnings per Share",
    34: "Interim Financial Reporting",
    36: "Impairment of Assets",
    37: "Provisions, Contingent Liabilities and Contingent Assets",
    38: "Intangible Assets",
    40: "Investment Property",
    41: "Agriculture",
    101: "First-time Adoption of Indian Accounting Standards",
    102: "Share-based Payment",
    103: "Business Combinations",
    104: "Insurance Contracts",
    105: "Non-current Assets Held for Sale and Discontinued Operations",
    106: "Exploration for and Evaluation of Mineral Resources",
    107: "Financial Instruments: Disclosures",
    108: "Operating Segments",
    109: "Financial Instruments",
    110: "Consolidated Financial Statements",
    111: "Joint Arrangements",
    112: "Disclosure of Interests in Other Entities",
    113: "Fair Value Measurement",
    114: "Regulatory Deferral Accounts",
    115: "Revenue from Contracts with Customers",
    116: "Leases",
    117: "Insurance Contracts",
}
