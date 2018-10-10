function save_team_admin() {
    var teamFormAdmin = { //Fetch form data
        'paymentid'   : $('#team_form_admin #id_paymentid').val(),
        'category'    : $('#team_form_admin #id_category').val(),
        'start_number': $('#team_form_admin #id_start_number').val(),
        'get_package' : $('#team_form_admin #id_get_package').is(":checked"),
        'get_number'  : $('#team_form_admin #id_get_number').is(":checked"),
        'give_paper'  : $('#team_form_admin #id_give_paper').is(":checked"),
        'start_time'  : $('#team_form_admin #id_start_time').val(),
        'finish_time' : $('#team_form_admin #id_finish_time').val(),
        'give_photos' : $('#team_form_admin #id_give_photos').is(":checked"),
        'penalty'     : $('#team_form_admin #id_penalty').val(),
        'dnf'         : $('#team_form_admin #id_dnf').is(":checked"),
        'csrfmiddlewaretoken' : csrf_token,
    };
    $.ajax({
        type      : 'POST',
        url       : '/team_admin',
        data      : teamFormAdmin,
        dataType  : 'json',
        success   : function(data) {
            if (data.success) {
                $('#team_form_admin_alert').html("Данные команды сохранены");
                $('#team_form_admin_alert').removeClass("alert-danger");
                $('#team_form_admin_alert').addClass("alert-success");
                $('#team_form_admin_alert').show();
                $("#team_form_admin_alert").fadeOut(3000);
            }
            else
            {
                $('#team_form_admin_alert').html("Упс, что-то пошло не так!");
                $('#team_form_admin_alert').removeClass("alert-success");
                $('#team_form_admin_alert').addClass("alert-danger");
                $('#team_form_admin_alert').show();
                $("#team_form_admin_alert").fadeOut(3000);
            }
        },
        error: function(XMLHttpRequest, textStatus, errorThrown) { 
            $('#team_form_admin_alert').html("Упс, что-то пошло не так: " + errorThrown);
            $('#team_form_admin_alert').removeClass("alert-success");
            $('#team_form_admin_alert').addClass("alert-danger");
            $('#team_form_admin_alert').show();
            $("#team_form_admin_alert").fadeOut(3000);
        }
    });
};

$('#team_form_admin').submit(function(e){
    e.preventDefault();
    save_team_admin("");
});