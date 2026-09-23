from ticket_bot.platforms.kktix_parser import (
    build_registration_url,
    detect_kktix_challenge,
    parse_event_page,
    parse_order_page,
    parse_registration_page,
)


KKTIX_EVENT_HTML = """
<!doctype html>
<html lang="zh-tw">
  <head>
    <title>《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》</title>
    <script type="application/ld+json">[
      {
        "@context":"http://schema.org",
        "@type":"Event",
        "name":"《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》",
        "url":"https://carrier.kktix.cc/events/476cd237",
        "startDate":"2026-04-03T16:00:00.000+08:00",
        "offers":[
          {"@type":"Offer","name":"身心障礙票","price":2940.0,"priceCurrency":"TWD","availability":"SoldOut"},
          {"@type":"Offer","name":"全票","price":5880.0,"priceCurrency":"TWD","availability":"InStock"}
        ]
      }
    ]</script>
  </head>
  <body>
    <div class="header-title"><h1>《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》</h1></div>
    <div class="event-info">
      <span class="timezoneSuffix">2026/04/03(周五) 16:00(+0800)</span>
      <span class="info-desc"><i class="fa fa-map-marker"></i> 臺灣大學綜合體育館1樓 / 臺北市大安區羅斯福路四段1號</span>
    </div>
    <div class="organizers mobi-hide clearfix">
      <i class="fa fa-sitemap"></i> 主辦單位
      <a href="https://carrier.kktix.cc">開麗娛樂經紀有限公司</a>
    </div>
    <div class="navbar-container">
      <li id="order-now"><a href="https://kktix.com/events/476cd237/registrations/new">立即購票</a></li>
    </div>
    <div class="description">
      本演唱會採實名制，付款方式僅限刷卡。
      購票時，需要輸入您的姓名、手機、身分證字號。
      JJINGGU官方第二期會員(GL)預購時，需輸入 Membership Number 才能進行優先購票。
      購票時可選擇【自行選位/電腦配位】。
      本節目網站購票僅接受已完成手機號碼及電子郵件地址驗證之會員購買。
    </div>
  </body>
</html>
"""


KKTIX_REGISTRATION_HTML = """
<!doctype html>
<html lang="en-us">
  <head>
    <title>《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》 Reserve Tickets</title>
  </head>
  <body class="registration-horizontal">
    <div class="page no-sidebar registration tng-registration">
      <div ng-if="showProgress" class="narrow-wrapper step-bar-wrapper">
        <div class="step-bar">
          <ul>
            <li class="active"><span><span class="step">1</span>Choose Ticket Type</span></li>
            <li><span><span class="step">2</span>Booking</span></li>
            <li><span><span class="step">3</span>Fill out the Form</span></li>
            <li><span><span class="step">4</span>Pay and Pickup</span></li>
          </ul>
        </div>
      </div>
      <div class="alert-wrapper align-center alert-info">
        <div class="alert">
          <span>Successfully logged in. Only KKTIX members who have completed mobile verification can purchase.</span>
          <button type="button" class="close">×</button>
        </div>
      </div>
      <div id="registrationsNewApp" class="main-wrapper clearfix">
        <table class="table table-striped"><tbody>
          <tr><th>Start Time</th><td>2026/04/03 16:00 (+0800)</td></tr>
          <tr><th>Event Location</th><td>臺灣大學綜合體育館1樓 / 臺北市大安區羅斯福路四段1號</td></tr>
          <tr><th>Event Host</th><td>開麗娛樂經紀有限公司</td></tr>
          <tr><th>Ticket Types</th><td>FamiPort Taiwan</td></tr>
          <tr><th>Payment Terms</th><td>Credit Card</td></tr>
        </tbody></table>
        <div class="arena-ticket-wrapper" ng-controller="TicketsListCtrl">
          <div class="arena-ticket-layout">
            <div class="arena-wrapper" arenas-map=""></div>
            <div class="ticket-list-wrapper with-seat">
              <div class="ticket-list">
                <div class="ticket-unit">
                  <div class="display-table" id="ticket_1004800">
                    <div class="display-table-row">
                      <span class="ticket-name">全票<div class="small text-muted">A1, A2, A3</div></span>
                      <span class="ticket-price"><span>TWD$5,880</span></span>
                      <span class="ticket-quantity">
                        <button class="btn-default minus" ng-click="quantityBtnClick(-1)" disabled="disabled"></button>
                        <input type="text" value="0">
                        <button class="btn-default plus" ng-click="quantityBtnClick(1)"></button>
                      </span>
                    </div>
                  </div>
                </div>
                <div class="ticket-unit">
                  <div class="display-table" id="ticket_1004803">
                    <div class="display-table-row">
                      <span class="ticket-name">身心障礙票<i class="fa fa-info-circle" title="You must have a disability identity to purchase/pre-register."></i><div class="small text-muted">身障席</div></span>
                      <span class="ticket-price"><span>TWD$2,940</span></span>
                      <span class="ticket-quantity">Sold Out</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div class="control-group">
              <label for="person_agree_terms" class="checkbox-inline">
                <input id="person_agree_terms" type="checkbox" value="agree">
                I've read and agreed to
                <a href="https://kktix.com/terms">Terms of Service</a> and
                <a href="https://kktix.com/policy">Privacy Policy</a>
              </label>
            </div>
            <div class="spinner-holder">
              <button ng-click="challenge()" disabled="disabled"><span>Pick Your Seat(s)</span></button>
              <button ng-click="challenge(1)" disabled="disabled"><span>Best Available</span></button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <script>
      var TIXGLOBAL;
      TIXGLOBAL = {
        queueApi: { host: "queue.kktix.com", enable: true },
        pageInfo: {
          recaptcha: {
            sitekeyNormal: 'normal-key',
            sitekeyAdvanced: 'advanced-key'
          }
        }
      }
    </script>
    <script>
      grecaptcha = { enterprise: {} };
    </script>
  </body>
</html>
"""


KKTIX_ORDER_HTML = """
<!doctype html>
<html lang="en-us">
  <head>
    <title>《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》 Reserve Tickets</title>
  </head>
  <body>
    <div ng-switch-when="countingDown" class="countdown-block">
      Your order has been reserved. Please fill out the information and confirm the order in 03:20 mins. Our system will cancel the order if not getting your confirmation after this time period.
    </div>
    <div ng-if="isPending">
      <a href="javascript:void(0);" confirmed-click="rechooseTicket()" ng-confirm-click="To re-select the ticket, your current order will be first canceled. Do you really want to re-select the ticket?" class="btn btn-default reselect-ticket">
        Cancel Ticket
      </a>
      <table class="table data-list cart-ticket-list">
        <tbody>
          <tr>
            <td class="ticket-name">全票</td>
            <td class="ticket-data" colspan="2">
              <table class="cart-ticket-list-subtable with-seat">
                <tbody>
                  <tr>
                    <td ng-if="hasArena()" class="seat-info">
                      <ul><li>Area A1, Row 11, No. 69</li></ul>
                    </td>
                    <td class="align-right price-count">TWD$5,880 x 1</td>
                    <td class="align-right price-total">TWD$5,880</td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
        </tbody>
        <tfoot>
          <tr class="highlight">
            <th class="ng-binding">Total Amount</th>
            <td colspan="2">TWD$5,880</td>
          </tr>
        </tfoot>
      </table>
      <div class="contact-info">
        <input type="text" name="contact[field_text_984583]">
        <input type="text" name="contact[field_email_984584]">
        <input type="text" name="contact[field_text_984585]">
        <select name="contact[field_select_984606]"></select>
        <input type="text" name="contact[field_text_984607]">
        <select name="contact[field_select_984608]"></select>
      </div>
      <div class="attendee-info">
        <div class="control-group">Seat Information</div>
        <input type="text" name="attendees[0][field_text_984609]">
        <input type="text" name="attendees[0][field_text_984611]">
        <input type="text" name="attendees[0][field_idnumber_984612]">
        <input type="radio" name="attendees[0][field_radio_984617]" value="471433">
      </div>
      <section class="additional-info">
        <label><input type="checkbox"> Show that you've been to this event on public page.</label>
        <label><input type="checkbox"> To be a fan of 開麗娛樂經紀有限公司</label>
      </section>
      <div class="form-actions plain align-center">
        <a class="btn btn-primary btn-lg" ng-click="confirmOrder()">Confirm Form</a>
      </div>
    </div>
  </body>
</html>
"""


def test_build_registration_url_from_kktix_cc_page():
    assert (
        build_registration_url("https://carrier.kktix.cc/events/476cd237")
        == "https://kktix.com/events/476cd237/registrations/new"
    )


def test_parse_event_page_extracts_core_fields():
    info = parse_event_page(KKTIX_EVENT_HTML, "https://carrier.kktix.cc/events/476cd237")

    assert info["title"] == "《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》"
    assert info["registration_url"] == "https://kktix.com/events/476cd237/registrations/new"
    assert info["organizer"] == "開麗娛樂經紀有限公司"
    assert info["start_at"] == "2026/04/03(周五) 16:00(+0800)"
    assert "臺灣大學綜合體育館1樓" in info["venue"]
    assert len(info["offers"]) == 2
    assert info["flags"]["requires_real_name"] is True
    assert info["flags"]["requires_membership_number"] is True
    assert info["flags"]["has_seat_selection"] is True
    assert info["flags"]["requires_phone_verification"] is True
    assert info["flags"]["credit_card_only"] is True
    assert info["challenge"] is False


def test_detect_kktix_challenge():
    html = """
    <html><head><title>Just a moment...</title></head>
    <body><script>window._cf_chl_opt = {};</script>
    <noscript>Enable JavaScript and cookies to continue</noscript></body></html>
    """
    assert detect_kktix_challenge(html) is True


def test_parse_registration_page_extracts_ticket_structure():
    info = parse_registration_page(
        KKTIX_REGISTRATION_HTML,
        "https://kktix.com/events/476cd237/registrations/new",
    )

    assert info["title"] == "《2026 ONEW FANMEETING [TOUGH LOVE] in TAIPEI》 Reserve Tickets"
    assert info["is_registration_page"] is True
    assert info["event_info"]["payment_terms"] == "Credit Card"
    assert info["event_info"]["ticket_types"] == "FamiPort Taiwan"
    assert info["progress_steps"] == [
        "Choose Ticket Type",
        "Booking",
        "Fill out the Form",
        "Pay and Pickup",
    ]
    assert info["terms_checkbox_id"] == "person_agree_terms"
    assert info["action_buttons"] == ["Pick Your Seat(s)", "Best Available"]
    assert info["flags"]["logged_in"] is True
    assert info["flags"]["requires_mobile_verification"] is True
    assert info["flags"]["has_seat_map"] is True
    assert info["flags"]["has_queue_api"] is True
    assert info["flags"]["protected_by_recaptcha"] is True
    assert info["queue_host"] == "queue.kktix.com"
    assert info["recaptcha"]["normal_sitekey"] == "normal-key"
    assert info["recaptcha"]["advanced_sitekey"] == "advanced-key"
    assert info["endpoints"]["queue_create_order"] == "https://queue.kktix.com/queue/476cd237"
    assert len(info["ticket_units"]) == 2
    assert info["ticket_units"][0] == {
        "ticket_id": "1004800",
        "name": "全票",
        "label": "A1, A2, A3",
        "price": "TWD$5,880",
        "status": "available",
        "selectable": True,
        "has_plus_button": True,
        "requires_disability_identification": False,
    }
    assert info["ticket_units"][1]["status"] == "sold_out"
    assert info["ticket_units"][1]["requires_disability_identification"] is True


def test_parse_order_page_extracts_pending_order_structure():
    info = parse_order_page(
        KKTIX_ORDER_HTML,
        "https://kktix.com/events/476cd237/registrations/154207929-e2ec3771358fd35ae023c21b0fe20a53#/",
    )

    assert info["is_order_page"] is True
    assert info["challenge"] is False
    assert info["flags"]["is_reserved_pending"] is True
    assert info["flags"]["has_cancel_ticket"] is True
    assert info["flags"]["has_confirm_form"] is True
    assert info["flags"]["has_real_name_fields"] is True
    assert info["flags"]["shows_seat_information"] is True
    assert info["flags"]["supports_public_attendance_toggle"] is True
    assert info["flags"]["supports_org_fan_toggle"] is True
    assert info["order_summary"] == {
        "ticket_name": "全票",
        "seat_info": "Area A1, Row 11, No. 69",
        "price_count": "TWD$5,880 x 1",
        "price_total": "TWD$5,880",
        "total_amount": "TWD$5,880",
    }
    assert info["contact_field_names"] == [
        "contact[field_text_984583]",
        "contact[field_email_984584]",
        "contact[field_text_984585]",
        "contact[field_select_984606]",
        "contact[field_text_984607]",
        "contact[field_select_984608]",
    ]
    assert info["attendee_field_names"] == [
        "attendees[0][field_text_984609]",
        "attendees[0][field_text_984611]",
        "attendees[0][field_idnumber_984612]",
        "attendees[0][field_radio_984617]",
    ]
    assert info["selectors"]["cancel_ticket"] == "a.reselect-ticket"
    assert info["selectors"]["confirm_form"] == "[ng-click='confirmOrder()']"
    assert info["endpoints"]["cancel_leave"] == (
        "https://kktix.com/events/476cd237/registrations/154207929-e2ec3771358fd35ae023c21b0fe20a53/leave"
    )
    assert info["endpoints"]["confirm_update_iframe"] == (
        "https://kktix.com/events/476cd237/registrations/154207929-e2ec3771358fd35ae023c21b0fe20a53?X-Requested-With=IFrame"
    )
    assert info["endpoints"]["base_info"] == "https://kktix.com/g/events/476cd237/base_info"
    assert info["endpoints"]["register_info"] == "https://kktix.com/g/events/476cd237/register_info"
