from app.core.outlook.outlook_service import OutlookService


def test_clean_recipients_excludes_main_duplicates_and_my_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["Ana <ana@empresa.com>, yo@empresa.com"],
        cc_list=["ana@empresa.com; Luis <luis@empresa.com>; yo@empresa.com"],
        main_recipient="ana@empresa.com",
        my_email="yo@empresa.com",
    )

    assert main == "ana@empresa.com"
    assert cc == ["luis@empresa.com"]


def test_clean_recipients_main_is_cleared_if_my_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["yo@empresa.com"],
        cc_list=["Otro <otro@empresa.com>"],
        main_recipient="yo@empresa.com",
        my_email="yo@empresa.com",
    )

    assert main == ""
    assert cc == ["otro@empresa.com"]


def test_clean_recipients_excludes_configured_user_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["a.sanchez@sansebas.es"],
        cc_list=["Otro <otro@empresa.com>; a.sanchez@sansebas.es"],
        main_recipient="a.sanchez@sansebas.es",
        my_email="",
    )

    assert main == ""
    assert cc == ["otro@empresa.com"]
