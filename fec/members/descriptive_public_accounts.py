"""Narrow editorial allowlist of self-contained public-account sentences.

These exact sentences were checked against the retained Congressional Record
excerpts. They are public self-reports, not independently verified biography,
recurring communication patterns, or evidence of private-meeting preferences.
The generator must find the exact sentence in the member's evidence and retain
that record's source, date, and locator before publishing it.
"""

PUBLIC_ACCOUNTS = {
    "Adam Schiff": (
        "I can still remember the wonder in my friend's eyes when I announced that we were moving to the Golden State."
    ),
    "Amy Klobuchar": (
        "My grandpa worked most of his life in the mines as did so many friends."
    ),
    "Andy Kim": (
        "I remember 2 days after January 6, I drove back home from the Capitol, back home to New Jersey."
    ),
    "Catherine Cortez Masto": (
        "I say this time and time again: My grandfather was from Chihuahua, Mexico."
    ),
    "Chip Roy": (
        "My grandfather was the chief of police of a small town in West Texas back in the 1940s."
    ),
    "Christopher Coons": (
        "I grew up in the shadow of the Vietnam war."
    ),
    "David McCormick": (
        "I grew up in a small town called Bloomsburg--the majority leader visited--in Northeastern Pennsylvania."
    ),
    "Edward Markey": (
        "My father drove a truck for the Hood milk company."
    ),
    "James Justice": (
        "I grew up in the little town of Beckley, WV."
    ),
    "Jim Banks": (
        "I grew up in a trailer park, the son of a factory worker in a small town in Indiana."
    ),
    "John Mannion": (
        "My father always said that a job is more than a paycheck."
    ),
    "Jon Husted": (
        "I was adopted by my loving parents, Jim and Judy Husted."
    ),
    "Lauren Underwood": (
        "I grew up in northern Illinois where I love being surrounded by the gorgeous corn and soybean fields."
    ),
    "Rick Scott": (
        "I grew up in public housing and watched my parents struggle to make ends meet."
    ),
    "Steve Daines": (
        "I grew up as the grandson of a Democrat in Montana."
    ),
    "Terri Sewell": (
        "I grew up in Selma in the shadow of the civil rights movement."
    ),
    "Tim Kaine": (
        "I grew up in a house with a dad who worked for a huge company, and it was his dream to start his own business."
    ),
    "Veronica Escobar": (
        "My family has lived on the border for more than 100 years."
    ),
    "Yassamin Ansari": (
        "My father came to the United States as a student."
    ),
}
