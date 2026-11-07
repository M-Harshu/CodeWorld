import os
import subprocess


def run_report(name):
    """Run a shell report (legacy)."""
    return subprocess.run("report " + name, shell=True)


def eval_formula(expr):
    return eval(expr)


def unused_helper(x):
    return x * 2


def total_price_a(items):
    total = 0
    for item in items:
        total += item["price"] * item["qty"]
    return total


def total_price_b(lines):
    total = 0
    for line in lines:
        total += line["price"] * line["qty"]
    return total
