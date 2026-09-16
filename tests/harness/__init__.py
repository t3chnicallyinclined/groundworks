# -*- coding: utf-8 -*-
"""The test harness: tools that check whether the tests themselves do their work (docs/TESTING.md).

    mutate.py      the mutation runner and its registry (TESTING s.2)
    gen.py         the seed-reproducible generator of valid configs and corpora (TESTING s.6)
    props.py       the standard-library property runner with shrinking and saved failures (TESTING s.6)

Nothing under xscholar/ imports this package. Standard library only.
"""
