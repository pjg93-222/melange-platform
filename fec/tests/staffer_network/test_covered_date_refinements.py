from fec.staffer_network.refine_covered_dates import assess, endpoint


def test_written_month_dates_preserve_precision_and_reject_invalid_days():
    assert endpoint('September 2019')=='2019-09-00'
    assert endpoint('Jan. 2nd, 2018')=='2018-01-02'
    result=assess('Legislative Director (March 2019 to February 2022)','2026-09-08')
    assert result['status']=='BOUNDED_SOURCE_INTERVAL'
    assert result['start']=='2019-03-31'
    assert result['employment_promoted'] is False
    assert assess('Jan 35, 2018 - March 2019','2026-09-08')['status']=='INVALID_SOURCE_DATE'


def test_does_not_pick_one_job_in_multi_job_text_or_expand_present():
    assert assess('January 2016 to July 2019; July 2019 to Feb. 2021','2026-09-08')['status']=='MULTIPLE_INTERVALS_REVIEW'
    assert assess('March 2019 to February 2022; 2010-2012','2026-09-08')['status']=='MULTIPLE_INTERVALS_REVIEW'
    assert assess('Jan 2018 to Present','2026-09-08') is None
    assert assess('02/19 - 03/21','2026-09-08') is None
