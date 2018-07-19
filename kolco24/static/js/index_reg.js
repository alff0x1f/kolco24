$('#reg_6h').click(function(){
    $('#reg_6h').addClass("active");
    $('#reg_12h').removeClass("active");
    $('#reg_24h').removeClass("active");
    $('#dist').val('6h');
});
$('#reg_12h').click(function(){
    $('#reg_6h').removeClass("active");
    $('#reg_12h').addClass("active");
    $('#reg_24h').removeClass("active");
    $('#dist').val('12h');
});
$('#reg_24h').click(function(){
    $('#reg_6h').removeClass("active");
    $('#reg_12h').removeClass("active");
    $('#reg_24h').addClass("active");
    $('#dist').val('24h');
    v = parseInt($('#ucount').val())
    if (v < 4){$('#ucount').val(4);$('#ucount').change()}
});

$('#ucount').on('change', function() {
    v = parseInt(this.value);
    $('#ucountlabel').text(v);
    $('#sumlabel').text(v*cost);
});