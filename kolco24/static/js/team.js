$( "#dist_header" ).click(function() {
    switch (dist) {
        case "6h":
            dist = "12h";
            $("#dist_header").text("12ч");
            break;
        case "12h":
            dist = "24h";
            $("#dist_header").text("24h");
            break;
        case "24h":
            dist = "6h";
            $("#dist_header").text("6h");
            break;
        default:
            break;
    }
    // alert( "Handler for .click() called." );
});

function set_ucount(count) {
    ucount = count;
    $("#ucountlabel").text(count);
    $("#sumlabel").text(count * cost);
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
        $("#del_member2").removeClass('d-none');
        $("#add_member3").removeClass('d-none');
    }
    if (count == 3){
        $("#del_member3").removeClass('d-none');
        $("#add_member4").removeClass('d-none');
    }
    if (count == 4){
        $("#del_member4").removeClass('d-none');
        $("#add_member5").removeClass('d-none');
    }
    if (count == 5){
        $("#del_member5").removeClass('d-none');
        $("#add_member6").removeClass('d-none');
    }
    if (count == 6){
        $("#del_member6").removeClass('d-none');
    }
  };
  
$(function() {
    set_ucount(ucount);
});

$('#teamform').submit(function(e){
    e.preventDefault();

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
            // 'paymentid'		: $('#teamform #paymentid').val(),
            // 'paymentmethod': $('#teamform #paymentmethod').val(),
    };

    $.ajax({
            type      : 'POST',
            url       : '/team',
            data      : teamForm,
            dataType  : 'json',
            success   : function(data) {
                            if (data.success) {
                                $('#team_form_alert').hide();
                                $('#team_form_alert').html("Данные команды сохранены");
                                $('#team_form_alert').removeClass("alert-danger");
                                $('#team_form_alert').addClass("alert-success");
                                $('#team_form_alert').show();
                                $("#team_form_alert").fadeOut(3000);
                                // $('#teamform #paymentid').val(data.paymentid);
                                // if (data.sum != 0){
                                //     if (data.paymentmethod == 'yandexmoney'){
                                //         $("#yasum").val(data.sum);
                                //         $("#yatargets").val("Взнос за Кольцо-24 (" + data.paymentid +")");
                                //         $("#yalabel").val(data.paymentid);
                                //         $('#yandexform').submit();
                                //     } else if (data.paymentmethod == 'c2c'){
                                //         $('#cti_sumlabel').text(data.sum);
                                //         $('#cti_sumlabel2').text(data.sum);
                                //         $('#cti_bankname').text(data.bankname);
                                //         $('#cti_bankname2').text(data.bankname);
                                //         $('#cti_phone').text(data.phone);
                                //         $('#cti_cardnumber').text(data.cardnumber);
                                //         $('#cti_name').text(data.name);
                                //         $('#comment').text(data.comment);
                                //         $('#card_transfer_instructions').show();
                                //         $('html, body').animate({
                                //                 scrollTop: $("#paymentmethodlist").offset().top + 100
                                //         }, 1000);
                                //     }
                                // }
                            }
                            else
                            {
                                $('#registration-msg .alert').html("Упс, что-то пошло не так!");
                                $('#registration-msg .alert').removeClass("alert-success");
                                $('#registration-msg .alert').addClass("alert-danger");
                                $('#registration-msg').show();
                            }
                        }
        });
});