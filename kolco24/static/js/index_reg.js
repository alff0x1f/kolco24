$('#reg_6h').click(function(){
    $('#reg_6h').addClass("active");
    $('#reg_12h').removeClass("active");
    $('#reg_24h').removeClass("active");
    $('#dist').val('6h');

    $('#ucount_2p').show();
    $('#ucount_3p').show();
    $('#ucount_4p').hide();
    $('#ucount_5p').hide();
    $('#ucount_6p').hide();
    v = parseInt($('#ucount').val());
    if (v > 3){$('#ucount').val(2);
    $('#ucount').change()}
});
$('#reg_12h').click(function(){
    $('#reg_6h').removeClass("active");
    $('#reg_12h').addClass("active");
    $('#reg_24h').removeClass("active");
    $('#dist').val('12h');
    $('#ucount_2p').show();
    $('#ucount_3p').hide();
    $('#ucount_4p').show();
    $('#ucount_5p').show();
    $('#ucount_6p').show();
});
$('#reg_24h').click(function(){
    $('#reg_6h').removeClass("active");
    $('#reg_12h').removeClass("active");
    $('#reg_24h').addClass("active");
    $('#dist').val('24h');
    $('#ucount_2p').hide();
    $('#ucount_3p').hide();
    $('#ucount_4p').show();
    $('#ucount_5p').show();
    $('#ucount_6p').show();
    v = parseInt($('#ucount').val());
    if (v < 4){$('#ucount').val(4);
    $('#ucount').change()}
});

$('#ucount').on('change', function() {
    v = parseInt(this.value);
    if (v === 1) {
        $('#warning_alone').show()
        $('#dist_links').hide();
        $('#dist_select').removeClass('col-sm-6 col-md-6').addClass('col-sm-12 col-md-12');
    } else {
        $('#warning_alone').hide()
        $('#dist_links').show();
        $('#dist_select').removeClass('col-sm-12 col-md-12').addClass('col-sm-6 col-md-6');
    };
    $('#ucountlabel').text(v);
    $('#sumlabel').text(v*cost);
});

function get_cost_index() {
    let p_info = {'csrfmiddlewaretoken': csrf_token,};
    $.ajax({
        type      : 'POST',
        url       : '/api/v1/getcost',
        data      : p_info,
        dataType  : 'json',
        success   : function(data) {
            if (data.success && cost !== data.cost) {
                cost = data.cost;
                $('#slotcost').text((cost));
                $('#ucount').change();
            }
        },
        error: function(XMLHttpRequest, textStatus, errorThrown) {
        }
    });
}

// let timerId = setInterval(get_cost_index, 5000);