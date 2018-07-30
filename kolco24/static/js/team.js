$( "#dist_header" ).click(function() {
    switch (dist) {
        case "6h":
            dist = "12h";
            $("#dist_header").text("12ч");
            break;
        case "12h":
            dist = "24h";
            $("#dist_header").text("24h");
            if (ucount < 4) { ucount = 4 }
            set_ucount(ucount)
            break;
        case "24h":
            dist = "6h";
            $("#dist_header").text("6h");
            if (ucount > 3) { ucount = 3; }
            set_ucount(ucount)
            break;
        default:
            break;
    }
});

function set_ucount(count) {
    ucount = count;
    $("#ucountlabel").text(count - ucount_paid);
    $("#sumlabel").text((count - ucount_paid) * cost);
    $("#yasum").val((count - ucount_paid) * cost);
    $("#paidfor_count").text(ucount_paid);
    // hide members:
    if (count < 6) {
        $("#member6").addClass('d-none');
    } 
    if (count < 5) {
        $("#member5").addClass('d-none');
    }
    if (count < 4) {
        $("#member4").addClass('d-none');
    }
    if (count < 3) {
        $("#member3").addClass('d-none');
    }
    // show members and hide controls on top controls
    if (count >= 3){
        $("#member3").removeClass('d-none');
        $("#add_member3").addClass('d-none');
    }
    if (count >= 4){
        $("#member4").removeClass('d-none');
        $("#add_member4").addClass('d-none');
        $("#del_member3").addClass('d-none');
    }
    if (count >= 5){
        $("#member5").removeClass('d-none');
        $("#add_member5").addClass('d-none');
        $("#del_member4").addClass('d-none');
    }
    if (count >= 6){
        $("#member6").removeClass('d-none');
        $("#add_member6").addClass('d-none');
        $("#del_member5").addClass('d-none');
    }
    // show controls on last item
    if (count == 2){
        $("#add_member3").removeClass('d-none');
    }
    if (count == 3){
        if (dist != "24h"){
            $("#del_member3").removeClass('d-none');
        }
        if (dist != "6h"){
            $("#add_member4").removeClass('d-none');
        }
    }
    if (count == 4){
        if (dist != "24h"){
            $("#del_member4").removeClass('d-none');
        } else {
            $("#del_member4").addClass('d-none');
        }
        if (dist != "6h"){
            $("#add_member5").removeClass('d-none');
        }
    }
    if (count == 5) {
        $("#del_member5").removeClass('d-none');
        if (dist != "6h"){
            $("#add_member6").removeClass('d-none');
        }
    }
    if (count == 6){
        $("#del_member6").removeClass('d-none');
    }

    if (ucount_paid != 0){
        $("#paidfor").show();
        $("#paidfor_action").text("Доплатить");
    } else {
        $("#paidfor").hide();
        $("#paidfor_action").text("Итого");
    }

    if (ucount_paid >= ucount){
        $("#sidecolumn_paid").hide();
        $("#paid_explain").hide();
    } else {
        $("#sidecolumn_paid").show();
        $("#paid_explain").show();
    }
    $("#pay_sberbank").show();
    $("#sberbank_initial_explanation").show();
    $("#sberbank_pay_manual").hide();
    $("#pay_tinkoff").show();
    $("#tinkoff_initial_explanation").show();
    $("#tinkoff_pay_manual").hide();
  };

function save_team(payment_method) {
    var teamForm = { //Fetch form data
        'dist'        : dist,
        'ucount'      : ucount,
        'paymentid'   : $('#teamform #id_paymentid').val(),
        'name'        : $('#teamform #id_name').val(),
        'city'        : $('#teamform #id_city').val(),
        'organization': $('#teamform #id_organization').val(),
        'athlet1'     : $('#teamform #id_athlet1').val(),
        'birth1'      : $('#teamform #id_birth1').val(),
        'athlet2'     : $('#teamform #id_athlet2').val(),
        'birth2'      : $('#teamform #id_birth2').val(),
        'athlet3'     : $('#teamform #id_athlet3').val(),
        'birth3'      : $('#teamform #id_birth3').val(),
        'athlet4'     : $('#teamform #id_athlet4').val(),
        'birth4'      : $('#teamform #id_birth4').val(),
        'athlet5'     : $('#teamform #id_athlet5').val(),
        'birth5'      : $('#teamform #id_birth5').val(),
        'athlet6'     : $('#teamform #id_athlet6').val(),
        'birth6'      : $('#teamform #id_birth6').val(),
        'csrfmiddlewaretoken' : csrf_token,
        // 'get_requisites' : get_requisites,
        // 'paymentid'		: $('#teamform #paymentid').val(),
        'paymentmethod': payment_method,
    };
    $.ajax({
        type      : 'POST',
        url       : '/team',
        data      : teamForm,
        dataType  : 'json',
        success   : function(data) {
            if (data.success) {
                $('#team_form_alert').html("Данные команды сохранены");
                $('#team_form_alert').removeClass("alert-danger");
                $('#team_form_alert').addClass("alert-success");
                $('#team_form_alert').show();
                $("#team_form_alert").fadeOut(3000);

                if (data.paymentmethod == "visamc"){
                    $('#yandexform #paymenttype').val('AC');
                    $('#yandexform #yandexwallet').val(data.yandexwallet);
                    $('#yandexform #yasum').val(data.sum);
                    $('#yandexform').submit();
                };
                if (data.paymentmethod == "yandexmoney"){
                    $('#yandexform #paymenttype').val('PC');
                    $('#yandexform #yandexwallet').val(data.yandexwallet);
                    $('#yandexform #yasum').val(data.sum);
                    $('#yandexform').submit();
                }
                if (data.paymentmethod == "sberbank"){
                    $("#pay_sberbank").hide();
                    $("#sberbank_initial_explanation").hide();
                    $('#sberbank_phone').text(data.cardholder_phone);
                    $('#sberbank_phone2').text(data.cardholder_phone);
                    $('#sberbank_cardnumber').text(data.cardnumber);
                    $('#sberbank_name').text(data.cardholder_name);
                    $('#sberbank_sum').text(data.sum);
                    $('#sberbank_sum2').text(data.sum);
                    $('#sberbank_comment').text(data.payment_comment);
                    $('#sberbank_comment2').text(data.payment_comment);
                    $('#sberbank_comment3').text(data.payment_comment);
                    $('#sberbank_comment4').text(data.payment_comment);
                    $("#sberbank_pay_manual").show();
                    $('html, body').animate({
                        scrollTop: $("#sberbank_pay_manual").offset().top-100
                      }, 300);
                }
                if (data.paymentmethod == "tinkoff"){
                    $("#pay_tinkoff").hide();
                    $("#tinkoff_initial_explanation").hide();
                    $('#tinkoff_phone').text(data.cardholder_phone);
                    $('#tinkoff_phone2').text(data.cardholder_phone);
                    $('#tinkoff_cardnumber').text(data.cardnumber);
                    $('#tinkoff_name').text(data.cardholder_name);
                    $('#tinkoff_sum').text(data.sum);
                    $('#tinkoff_comment').text(data.payment_comment);
                    $('#tinkoff_comment2').text(data.payment_comment);
                    $("#tinkoff_pay_manual").show();
                    $('html, body').animate({
                        scrollTop: $("#tinkoff_pay_manual").offset().top-100
                      }, 300);
                }
            }
            else
            {
                $('#team_form_alert').html("Упс, что-то пошло не так!");
                $('#team_form_alert').removeClass("alert-success");
                $('#team_form_alert').addClass("alert-danger");
                $('#team_form_alert').show();
                $("#team_form_alert").fadeOut(3000);
            }
        },
        error: function(XMLHttpRequest, textStatus, errorThrown) { 
            $('#team_form_alert').html("Упс, что-то пошло не так: " + errorThrown);
            $('#team_form_alert').removeClass("alert-success");
            $('#team_form_alert').addClass("alert-danger");
            $('#team_form_alert').show();
            $("#team_form_alert").fadeOut(3000);
        }
    });
};

$(function() {
    set_ucount(ucount);
});

$('#teamform').submit(function(e){
    e.preventDefault();
    save_team("");
});

$('#pay_visamastercard').on('click', function(){
    $('#radio1').click();
    save_team("visamc");
});

$('#pay_yandexmoney').on('click', function(){
    $('#radio2').click();
    save_team("yandexmoney");
});

$('#pay_sberbank').on('click', function(){
    $('#radio3').click();
    save_team("sberbank");
});

$('#pay_tinkoff').on('click', function(){
    $('#radio4').click();
    save_team("tinkoff");
});

$('#common_paid').on('click', function(){
    payment_method = $('input[name=radio]:checked', '#paymentmethod').val()
    save_team(payment_method)
});